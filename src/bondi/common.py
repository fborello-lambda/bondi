"""Helpers shared by the bondi commands."""

import os
import sys
import time
from pathlib import Path

from rich.text import Text

from bondi import automations, generated, gitops, layout, state, team, ui
from bondi.entries import roots
from bondi.gitops import GitError
from bondi.manifest import MANIFEST, Manifest, ManifestError, load
from bondi.syncer import BLOCKED, CONFLICT, DEL_LIVE, DEL_REPO, TO_LIVE, TO_REPO, Action
from bondi.ui import Choice


class UserError(Exception):
    pass


LABELS = {
    TO_LIVE: ("update here", "cyan"),
    TO_REPO: ("save to profile", "green"),
    DEL_LIVE: ("delete here", "red"),
    DEL_REPO: ("delete in profile", "red"),
    CONFLICT: ("conflict", "bold yellow"),
    BLOCKED: ("left out", "bold red"),
}


def pick_claude_dir(arg: str | None) -> Path:
    if arg:
        return layout.expand(arg).absolute()
    default = layout.default_claude_dir()
    others = [d for d in layout.detect_claude_dirs() if d.resolve() != default.resolve()]
    if not others:
        return default
    if not ui.interactive():
        raise UserError(
            "this machine has more than one Claude folder ("
            + ", ".join(layout.collapse(d) for d in [default, *others])
            + "). Pass --claude-dir to pick one."
        )
    ui.note("This machine has more than one Claude folder, usually one per Claude account.")
    return ui.select("Which Claude folder?",
                     [Choice(layout.collapse(default), default, hint="default")]
                     + [Choice(layout.collapse(d), d) for d in others], default)


def installs() -> list[state.Install]:
    return state.load()


def manifest_of(inst: state.Install) -> Manifest:
    return load(inst.repo / MANIFEST)


def selected(names: list[str], claude_dir: str | None) -> list[state.Install]:
    found = installs()
    if not found:
        raise UserError("there is no profile on this machine yet. Add one with: bondi add <git-url>, "
                        "or ask Claude to create one (the bondi-create skill)")
    cdir = layout.expand(claude_dir).absolute() if claude_dir else None
    picked = [i for i in found if (not names or i.name in names) and (cdir is None or i.cdir.resolve() == cdir.resolve())]
    missing = set(names) - {i.name for i in picked}
    if missing:
        raise UserError(f"not on this machine: {', '.join(sorted(missing))}. Run bondi to see the profiles here")
    return picked


def overlaps(m: Manifest, cdir: Path, checkout: Path, skip_name: str | None = None,
             homes: dict[str, str] | None = None) -> list[str]:
    real = lambda p: Path(os.path.realpath(p))
    mine = [r.live for r in roots(m, cdir, checkout, homes=homes)] + team.output_paths(m, cdir)
    repos = {p.github.lower() for p in m.projects}
    errs = []
    for inst in installs():
        if inst.name == skip_name or inst.cdir.resolve() != cdir.resolve() or not (inst.repo / MANIFEST).exists():
            continue
        other = manifest_of(inst)
        errs += [f"project memory for {p.github} already belongs to profile '{inst.name}'"
                 for p in other.projects if p.github.lower() in repos]
        theirs = [r.live for r in roots(other, inst.cdir, inst.repo, inst.base)] + team.output_paths(other, inst.cdir)
        for a in mine:
            for b in theirs:
                ra, rb = real(a), real(b)
                if ra == rb or ra in rb.parents or rb in ra.parents:
                    errs.append(f"{layout.collapse(a)} already belongs to profile '{inst.name}'")
    return sorted(set(errs))


def show_plan(actions: list[Action], verbose: bool = False) -> None:
    if not actions:
        ui.done("Files are in sync.")
        return
    limit = None if verbose else 25
    rows = [[Text(LABELS[a.op][0], style=LABELS[a.op][1]), layout.collapse(a.item.live), a.note] for a in actions[:limit]]
    ui.table(["Change", "File", "Note"], rows)
    if limit and len(actions) > limit:
        ui.note(f"... and {len(actions) - limit} more. Add -v to list all.")


def backup_dir() -> Path:
    return layout.backup_root() / time.strftime("%Y%m%d-%H%M%S")


def finish(inst: state.Install, message: str) -> None:
    if gitops.commit_all(inst.repo, message):
        try:
            gitops.push(inst.repo)
        except GitError as e:
            ui.warn(f"saved locally but could not push: {e}. The next sync pushes it.")
    elif gitops.remote_url(inst.repo):
        try:
            gitops.push(inst.repo)
        except GitError as e:
            ui.warn(f"could not push earlier local changes: {e}. The next sync retries.")
    inst.base = gitops.head(inst.repo)
    inst.synced_at = time.time()


def upsert(inst: state.Install) -> None:
    others = [i for i in installs() if not (i.name == inst.name and i.cdir.resolve() == inst.cdir.resolve())]
    state.save([*others, inst])


def apply_team(inst: state.Install, m: Manifest, bdir: Path, dry_run: bool = False) -> None:
    """Write the lead skill, role subagents and schedules skill, and report what changed."""
    try:
        wanted = team.outputs(m, inst.cdir, inst.repo)
    except FileNotFoundError as e:
        raise UserError(str(e)) from e
    for line in generated.apply(inst, wanted, bdir, dry_run):
        ui.say(f"  {line}")


def automation_hints(inst: state.Install, m: Manifest) -> None:
    local = [r for r in m.routines if r.runner == "local"]
    remote = [r for r in m.routines if r.runner != "local"]
    if local and automations.scheduler_status() == "off":
        offer_scheduler(len(local))
    if remote:
        ui.hint(f"{len(remote)} desktop or cloud routine(s) to register. In Claude, run: /{m.name}-schedules")
    cmd = team.start_command(inst.cdir, m, layout.home() / ".claude")
    if cmd:
        ui.hint(f"Start a lead session: {cmd}")


def offer_scheduler(count: int) -> None:
    """Ask once, in a terminal only: turning the scheduler on installs a launchd agent or a crontab line."""
    prefs_file = state.routines_file().with_name("prefs.json")
    prefs = state.read_json(prefs_file, {})
    if prefs.get("scheduler") == "declined":
        return
    if not ui.interactive():
        ui.hint(f"{count} scheduled task(s) wait for the scheduler. Run bondi sync in a terminal to turn it on.")
        return
    how = "a launchd agent" if sys.platform == "darwin" else "a crontab line"
    _, warning = automations.bondi_command()
    if warning:
        ui.warn(warning)
    if ui.confirm(f"{count} scheduled task(s) need the scheduler: {how} that runs `bondi _tick` every minute. Turn it on?",
                  default=not warning):
        try:
            ui.done(f"Scheduler on ({automations.scheduler_on()}).")
        except (ManifestError, OSError) as e:
            ui.warn(f"could not turn the scheduler on: {e}")
    else:
        prefs["scheduler"] = "declined"
        state.write_json(prefs_file, prefs)
        ui.hint("bondi will not ask again. Turn it on later with: bondi _schedule on")


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def repo_remote(path: Path) -> str:
    return (gitops.remote_url(path) or "") if (path / ".git").exists() else ""
