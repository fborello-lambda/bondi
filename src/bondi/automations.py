"""Watches poll a shell check for free; routines run a prompt on a schedule."""

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from bondi import layout, state
from bondi.cron import matches
from bondi.manifest import AUTOMATIONS, ManifestError, Routine, Watch, load, parse_automations, seconds, MANIFEST


# ---------- running claude and notifying ----------

def claude_exe() -> str:
    saved = state.read_json(scheduler_file(), {}).get("claude")
    return os.environ.get("BONDI_CLAUDE") or shutil.which("claude") or saved or "claude"


def claude_cmd(model: str, tools: list[str]) -> list[str]:
    """The prompt goes on stdin: --allowedTools takes a list and would swallow a trailing prompt."""
    cmd = [claude_exe(), "-p", "--model", model]
    return cmd + (["--allowedTools", *tools] if tools else [])


def claude_env(cdir: Path | None) -> dict:
    env = dict(os.environ)
    if cdir:
        env["CLAUDE_CONFIG_DIR"] = str(cdir)
    return env


def notify(title: str, message: str, sound: str = "") -> None:
    custom = os.environ.get("BONDI_NOTIFY_CMD")
    try:
        if custom:
            subprocess.run([*shlex.split(custom), title, message], timeout=10)
        elif sys.platform == "darwin" and shutil.which("osascript"):
            # Text goes in as arguments, so quotes in a message can never break the script.
            script = ("on run argv\nif item 3 of argv is \"\" then\ndisplay notification (item 2 of argv) with title "
                      "(item 1 of argv)\nelse\ndisplay notification (item 2 of argv) with title (item 1 of argv) "
                      "sound name (item 3 of argv)\nend if\nend run")
            subprocess.run(["osascript", "-e", script, title[:80], message[:200], sound], timeout=10, capture_output=True)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=10, capture_output=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


# ---------- finding watches ----------

@dataclass
class Source:
    """Where a watch template comes from: an installed bondi, or the local automations file."""
    label: str
    cdir: Path | None
    watches: list[Watch]
    routines: list[Routine]
    checkout: Path | None = None


def sources(errors: list[str] | None = None) -> list[Source]:
    """Every place automations come from. A broken file is reported in errors and skipped."""
    out = []
    local = layout.local_automations()
    try:
        if local.is_file():
            w, r = parse_automations(local.read_text(), layout.collapse(local))
            out.append(Source("local", None, w, r, local.parent))
    except ManifestError as e:
        if errors is None:
            raise
        errors.append(str(e))
    for inst in state.load():
        try:
            if (inst.repo / MANIFEST).is_file():
                m = load(inst.repo / MANIFEST)
                out.append(Source(inst.name, inst.cdir, m.watches, m.routines, inst.repo))
        except ManifestError as e:
            if errors is None:
                raise
            errors.append(f"{inst.name}: {e}")
    return out


def find_watch(name: str) -> tuple[Source, Watch]:
    bondi, _, short = name.rpartition("/")
    # One broken profile must not hide every other profile's watches.
    hits = [(s, w) for s in sources([]) for w in s.watches if w.name == short and (not bondi or s.label == bondi)]
    if not hits:
        raise ManifestError(f"no watch named '{name}'. List them with: bondi _watches --list")
    if len({s.label for s, _ in hits}) > 1:
        raise ManifestError(f"'{short}' exists in {', '.join(s.label for s, _ in hits)}; name one, e.g. {hits[0][0].label}/{short}")
    return hits[0]


# ---------- watch runs ----------

@dataclass
class WatchRun:
    id: str
    watch: str
    source: str
    args: dict[str, str]
    check: str
    done_when: str
    every_s: int
    timeout_s: int
    notify: bool
    model: str
    then: str
    tools: list[str]
    cwd: str
    claude_dir: str | None
    pid: int | None = None
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    last_check_at: float | None = None
    last_output: str = ""
    checks: int = 0
    finished_at: float | None = None
    follow_up: str = ""

    @property
    def path(self) -> Path:
        return state.watches_dir() / f"{self.id}.json"

    @property
    def log(self) -> Path:
        return state.watches_dir() / f"{self.id}.log"

    def save(self) -> None:
        state.write_json(self.path, asdict(self))


def load_run(run_id: str) -> WatchRun:
    data = state.read_json(state.watches_dir() / f"{run_id}.json", None)
    if data is None:
        raise ManifestError(f"no watch run '{run_id}'. See: bondi _watches --list")
    return WatchRun(**data)


def runs() -> list[WatchRun]:
    d = state.watches_dir()
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(WatchRun(**state.read_json(p, {})))
        except TypeError:
            continue  # a run file from another bondi version, or damaged: skip it
    for r in out:
        if r.status == "running" and not _alive(r.pid, r.id):
            r.status = "stopped (process gone)"
    return sorted(out, key=lambda r: r.started_at, reverse=True)


def _alive(pid: int | None, run_id: str | None = None) -> bool:
    """True only if pid is running and, with run_id, is that watch's own poller (pids get reused)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    if run_id is None:
        return True
    r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return "_watch" in r.stdout and run_id in r.stdout


PLACEHOLDER = re.compile(r"\{([a-z0-9][a-z0-9-]*)\}")


def fill(template: str, values: dict[str, str], quote: bool = False) -> str:
    """Replace {name} placeholders in one pass, so a value can never introduce another placeholder."""
    def sub(m):
        if m.group(1) not in values:
            return m.group(0)
        return shlex.quote(values[m.group(1)]) if quote else values[m.group(1)]
    return PLACEHOLDER.sub(sub, template)


def _short(value: str) -> str:
    """A readable piece of an argument for run IDs and notifications: the last path part, trimmed."""
    tail = value.rstrip("/").rsplit("/", 1)[-1] if "/" in value else value
    return re.sub(r"[^A-Za-z0-9]+", "", tail)[:16] or "x"


def prepare(source: Source, w: Watch, values: list[str]) -> WatchRun:
    if len(values) != len(w.args):
        need = " ".join(f"<{a}>" for a in w.args) or "(no arguments)"
        raise ManifestError(f"watch '{w.name}' takes: {need}")
    args = dict(zip(w.args, values))
    check = fill(w.check, args, quote=True)
    return WatchRun(
        id=f"{w.name}-{'-'.join(_short(v) for v in values) or 'run'}-{uuid.uuid4().hex[:4]}",
        watch=w.name, source=source.label, args=args, check=check, done_when=w.done_when,
        every_s=seconds(w.every), timeout_s=seconds(w.timeout), notify=w.notify, model=w.model,
        then=w.then, tools=w.tools, cwd=w.cwd, claude_dir=layout.collapse(source.cdir) if source.cdir else None,
    )


def start(run: WatchRun) -> WatchRun:
    """Start the poller as a detached background process that survives the terminal."""
    state.watches_dir().mkdir(parents=True, exist_ok=True)
    run.save()
    with open(run.log, "a") as log:
        proc = subprocess.Popen([sys.executable, "-m", "bondi", "_watch", run.id], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(layout.home()))
    run.pid = proc.pid
    run.save()
    return run


def _log(run: WatchRun, msg: str) -> None:
    with open(run.log, "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def poll(run: WatchRun, sleep=time.sleep) -> WatchRun:
    """The watch loop: run the check every interval until done_when matches, or the timeout passes."""
    run.pid, run.status = os.getpid(), "running"
    run.save()
    deadline = run.started_at + run.timeout_s
    cwd = str(layout.expand(run.cwd)) if run.cwd else str(layout.home())
    while True:
        try:
            r = subprocess.run(run.check, shell=True, capture_output=True, text=True, cwd=cwd, timeout=min(120, run.every_s))
            out = (r.stdout.strip() or r.stderr.strip() or f"exit {r.returncode}")[:500]
        except subprocess.TimeoutExpired:
            out = "check timed out"
        run.checks, run.last_check_at, run.last_output = run.checks + 1, time.time(), out
        _log(run, f"check {run.checks}: {out}")
        if re.search(run.done_when, out):
            return _finish(run, "done", out)
        if load_run(run.id).status == "stopping":
            return _finish(run, "stopped", out)
        if time.time() + run.every_s > deadline:
            return _finish(run, "timed out", out)
        run.save()
        sleep(run.every_s)


def _finish(run: WatchRun, status: str, out: str) -> WatchRun:
    run.status, run.finished_at = status, time.time()
    label = f"{run.watch} {' '.join(_short(v) for v in run.args.values())}".strip()
    if status == "done" and run.then:
        prompt = fill(run.then, {**run.args, "output": out})
        cwd = str(layout.expand(run.cwd)) if run.cwd else str(layout.home())
        cdir = layout.expand(run.claude_dir) if run.claude_dir else None
        try:
            r = subprocess.run(claude_cmd(run.model, run.tools), input=prompt, capture_output=True, text=True,
                               cwd=cwd, env=claude_env(cdir), timeout=900)
            run.follow_up = (r.stdout.strip() or r.stderr.strip())[:4000]
        except (OSError, subprocess.SubprocessError) as e:
            run.follow_up = f"follow-up failed: {e}"
        _log(run, f"follow-up ({run.model}):\n{run.follow_up}")
    _log(run, f"{status}: {out}")
    if run.notify and status != "stopped":
        first = (run.follow_up.splitlines() or [out])[0] if run.follow_up else out
        notify(f"bondi: {label} {status}", first)
    run.save()
    return run


def stop(run_id: str) -> WatchRun:
    run = load_run(run_id)
    if run.status != "running":
        return run
    if _alive(run.pid, run.id):
        try:
            os.killpg(run.pid, signal.SIGTERM)  # the poller leads its own group, so a follow-up stops too
        except (ProcessLookupError, PermissionError):
            pass
    run.status, run.finished_at = "stopped", time.time()
    run.save()
    return run


# ---------- routines ----------

def routine_prompt(source: Source, r: Routine) -> str:
    if not r.prompt_file:
        return r.prompt
    path = (source.checkout or layout.home()) / r.prompt_file
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"routine '{r.name}': {r.prompt_file} is missing or is a link")
    return path.read_text().strip()


def routine_key(source: Source, r: Routine) -> str:
    where = layout.collapse(source.cdir) if source.cdir else "local"
    return f"{source.label}/{r.name}@{where}"


def local_routines(errors: list[str] | None = None) -> list[tuple[Source, Routine]]:
    return [(s, r) for s in sources(errors) for r in s.routines if r.runner == "local"]


def run_routine(source: Source, r: Routine, stamp: str | None = None) -> tuple[Path, int]:
    """Start one routine run in the background and return its log file."""
    stamp = stamp or datetime.now().strftime("%Y-%m-%dT%H:%M")
    state.logs_dir().mkdir(parents=True, exist_ok=True)
    log = state.logs_dir() / f"routine-{source.label}-{r.name}.log"
    cwd = str(layout.expand(r.cwd)) if r.cwd else str(layout.home())
    routine_prompt(source, r)
    with open(log, "a") as f:
        f.write(f"\n==== {stamp} {routine_key(source, r)} ({r.model})\n")
        f.flush()
        proc = subprocess.Popen([sys.executable, "-m", "bondi", "_routine", routine_key(source, r)], stdout=f,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd, start_new_session=True)
    return log, proc.pid


def run_routine_now(key: str) -> int:
    """The worker behind every routine run: run claude, wait for it, and notify."""
    hits = [(s, r) for s, r in local_routines([]) if routine_key(s, r) == key]
    if not hits:
        print(f"no local routine {key}")
        return 1
    source, r = hits[0]
    cwd = str(layout.expand(r.cwd)) if r.cwd else str(layout.home())
    try:
        res = subprocess.run(claude_cmd(r.model, r.tools), input=routine_prompt(source, r), capture_output=True,
                             text=True, cwd=cwd, env=claude_env(source.cdir), timeout=seconds(r.timeout))
        out, ok = (res.stdout.strip() or res.stderr.strip()), res.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = f"stopped after {r.timeout}", False
    except OSError as e:
        out, ok = str(e), False
    print(out)
    if r.notify:
        first = next((line for line in out.splitlines() if line.strip()), "no output")
        notify(f"bondi: {r.name} {'done' if ok else 'failed'}", first)
    return 0 if ok else 1


def tick(now: datetime | None = None) -> list[str]:
    """Run every local routine due this minute. The OS scheduler calls this once a minute."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    last = state.read_json(state.routines_file(), {})
    errors: list[str] = []
    started = []
    for source, r in [(s, r) for s in sources(errors) for r in s.routines if r.runner == "local"]:
        key = routine_key(source, r)
        prev = last.get(key, {})
        if prev.get("minute") == stamp or not matches(r.schedule, now):
            continue
        if _alive(prev.get("pid")):
            errors.append(f"{key}: the previous run is still going; skipped {stamp}")
            continue
        try:
            log, pid = run_routine(source, r, stamp)
        except (OSError, ManifestError) as e:
            errors.append(f"{key}: {e}")
            continue
        last[key] = {"minute": stamp, "log": layout.collapse(log), "pid": pid}
        started.append(key)
    if started:
        state.write_json(state.routines_file(), last)
    for e in errors:
        print(f"{stamp} bondi tick: {e}")
    return started


# ---------- the OS scheduler that calls `bondi _tick` ----------

LAUNCHD_LABEL = "bondi.tick"
CRON_TAG = "# bondi tick"


def bondi_command() -> tuple[list[str], str]:
    """How the scheduler should start bondi, plus a warning when that path will not last."""
    exe = shutil.which("bondi")
    if exe and "archive-v" not in exe:
        return [exe], ""
    cmd = [sys.executable, "-m", "bondi"]
    if "archive-v" in sys.executable or "/uv/" in sys.executable:
        return cmd, ("bondi is running from a temporary uvx environment, which uv may clean up. Install it "
                     "for routines: uv tool install git+https://github.com/fborello-lambda/bondi")
    return cmd, ""


def scheduler_file() -> Path:
    return state.routines_file().with_name("scheduler.json")


def _plist() -> Path:
    return layout.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def scheduler_status() -> str:
    if sys.platform == "darwin":
        return "on (launchd)" if _plist().is_file() else "off"
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:
        return "unavailable (no crontab here)"
    return "on (crontab)" if CRON_TAG in r.stdout else "off"


def _crontab() -> str:
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError as e:
        raise ManifestError("this machine has no crontab command; install cron to run scheduled tasks") from e
    if r.returncode != 0 and "no crontab" not in r.stderr.lower():
        raise ManifestError(f"cannot read your crontab, so bondi leaves it alone: {r.stderr.strip()}")
    return r.stdout if r.returncode == 0 else ""


def scheduler_on() -> str:
    cmd, _ = bondi_command()
    claude = shutil.which("claude")
    if not claude:
        raise ManifestError("the claude CLI is not on PATH, so routines could not run; install it first")
    path_env = os.environ.get("PATH", "/usr/bin:/bin")
    state.write_json(scheduler_file(), {"claude": claude, "path": path_env})
    if sys.platform == "darwin":
        log = state.logs_dir() / "tick.log"
        state.logs_dir().mkdir(parents=True, exist_ok=True)
        from xml.sax.saxutils import escape
        args = "".join(f"<string>{escape(a)}</string>" for a in [*cmd, "_tick"])
        env = (f"<key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(path_env)}</string>"
               f"<key>BONDI_CLAUDE</key><string>{escape(claude)}</string></dict>")
        _plist().parent.mkdir(parents=True, exist_ok=True)
        _plist().write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LAUNCHD_LABEL}</string>
<key>ProgramArguments</key><array>{args}</array>
{env}
<key>StartInterval</key><integer>60</integer>
<key>StandardOutPath</key><string>{escape(str(log))}</string>
<key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "unload", str(_plist())], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(_plist())], capture_output=True, text=True)
        if r.returncode != 0:
            raise ManifestError(f"launchctl could not load {_plist()}: {r.stderr.strip()}")
        return "launchd"
    lines = [ln for ln in _crontab().splitlines() if CRON_TAG not in ln]
    log = shlex.quote(str(state.logs_dir() / "tick.log"))
    state.logs_dir().mkdir(parents=True, exist_ok=True)
    env = f"PATH={shlex.quote(path_env)} BONDI_CLAUDE={shlex.quote(claude)}"
    lines.append(f"* * * * * {env} {' '.join(shlex.quote(c) for c in cmd)} _tick >>{log} 2>&1 {CRON_TAG}")
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
    return "crontab"


def scheduler_off() -> None:
    if sys.platform == "darwin":
        if _plist().is_file():
            subprocess.run(["launchctl", "unload", str(_plist())], capture_output=True)
            _plist().unlink()
        return
    lines = [ln for ln in _crontab().splitlines() if CRON_TAG not in ln]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + ("\n" if lines else ""), text=True, check=True)


def local_file() -> Path:
    return layout.local_automations()


def automations_path(checkout: Path) -> Path:
    return checkout / AUTOMATIONS
