"""bondi.toml: what one bondi carries. Paths are relative to the Claude folder, to ~, or to the bondi repo."""

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "bondi.toml"
AUTOMATIONS = "automations.toml"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MODELS = {"opus", "sonnet", "haiku", "fable", "inherit"}
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}
RUNNERS = {"local", "desktop", "cloud"}
DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")


class ManifestError(ValueError):
    pass


@dataclass
class Project:
    key: str
    github: str
    memory: bool = True


@dataclass
class Lead:
    name: str
    usage: str
    model: str = "opus"
    playbook: str = ""
    parallel: bool = True


@dataclass
class Role:
    name: str
    usage: str
    model: str = "inherit"
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    effort: str = ""
    prompt: str = ""
    prompt_file: str = ""


@dataclass
class Watch:
    name: str
    check: str
    done_when: str
    description: str = ""
    args: list[str] = field(default_factory=list)
    every: str = "2m"
    timeout: str = "24h"
    notify: bool = True
    model: str = "haiku"
    then: str = ""
    tools: list[str] = field(default_factory=list)
    cwd: str = ""


@dataclass
class Routine:
    name: str
    schedule: str
    description: str = ""
    runner: str = "local"
    model: str = "sonnet"
    prompt: str = ""
    prompt_file: str = ""
    tools: list[str] = field(default_factory=list)
    cwd: str = ""
    notify: bool = True
    timeout: str = "30m"


@dataclass
class Manifest:
    name: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    home_files: list[str] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    # Give spawned agents the repo's memory index through a SubagentStart hook.
    agent_memory: bool = True
    lead: Lead | None = None
    roles: list[Role] = field(default_factory=list)
    watches: list[Watch] = field(default_factory=list)
    routines: list[Routine] = field(default_factory=list)


def project_key(github: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", github.rsplit("/", 1)[-1].lower()).strip("-") or "project"


def seconds(duration: str) -> int:
    m = DURATION_RE.match(duration.strip())
    if not m:
        raise ManifestError(f"'{duration}' is not a duration like 30s, 2m, 1h or 1d")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _strs(value, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{where} must be a list of strings")
    return value


def _relative(p: str, where: str) -> str:
    clean = p.strip().strip("/")
    if not clean or not Path(clean).parts or p.startswith(("/", "~")) or ".." in Path(clean).parts:
        raise ManifestError(f"{where}: '{p}' must be a relative path inside its folder")
    return clean


def _name(value, where: str) -> str:
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ManifestError(f"{where} must be lowercase letters, digits and '-', at most 40 chars")
    return value


def _model(value: str, where: str) -> str:
    if value not in MODELS and not value.startswith("claude-"):
        raise ManifestError(f"{where}: model must be one of {', '.join(sorted(MODELS))}, or a claude-* model ID")
    return value


def _table(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a table")
    return value


def _unique(items, what: str) -> None:
    seen: set[str] = set()
    for i in items:
        if i.name in seen:
            raise ManifestError(f"two {what} are named '{i.name}'")
        seen.add(i.name)


def _parse_lead(raw) -> Lead | None:
    if raw is None:
        return None
    t = _table(raw, "[lead]")
    lead = Lead(name=_name(t.get("name", ""), "lead.name"), usage=str(t.get("usage", "")).strip(),
                model=_model(str(t.get("model", "opus")), "lead"), playbook=str(t.get("playbook", "")),
                parallel=bool(t.get("parallel", True)))
    if not lead.usage:
        raise ManifestError("lead.usage is required: one line on when to start a session as the lead")
    if lead.playbook:
        lead.playbook = _relative(lead.playbook, "lead.playbook")
    return lead


def _parse_role(i: int, raw) -> Role:
    t = _table(raw, f"roles[{i}]")
    r = Role(name=_name(t.get("name", ""), f"roles[{i}].name"), usage=str(t.get("usage", "")).strip(),
             model=str(t.get("model", "inherit")), tools=_strs(t.get("tools"), f"roles[{i}].tools"),
             skills=_strs(t.get("skills"), f"roles[{i}].skills"), effort=str(t.get("effort", "")),
             prompt=str(t.get("prompt", "")), prompt_file=str(t.get("prompt_file", "")))
    if not r.usage:
        raise ManifestError(f"role '{r.name}' needs usage: when the lead should hand work to it")
    _model(r.model, f"role '{r.name}'")
    if r.effort not in EFFORTS:
        raise ManifestError(f"role '{r.name}': effort must be one of {', '.join(sorted(EFFORTS - {''}))}")
    if r.prompt and r.prompt_file:
        raise ManifestError(f"role '{r.name}': set prompt or prompt_file, not both")
    if r.prompt_file:
        r.prompt_file = _relative(r.prompt_file, f"role '{r.name}' prompt_file")
    return r


def _parse_watch(i: int, raw) -> Watch:
    t = _table(raw, f"watches[{i}]")
    w = Watch(name=_name(t.get("name", ""), f"watches[{i}].name"), check=str(t.get("check", "")).strip(),
              done_when=str(t.get("done_when", "")).strip(), description=str(t.get("description", "")),
              args=_strs(t.get("args"), f"watches[{i}].args"), every=str(t.get("every", "2m")),
              timeout=str(t.get("timeout", "24h")), notify=bool(t.get("notify", True)),
              model=str(t.get("model", "haiku")), then=str(t.get("then", "")).strip(),
              tools=_strs(t.get("tools"), f"watches[{i}].tools"), cwd=str(t.get("cwd", "")))
    if not w.check or not w.done_when:
        raise ManifestError(f"watch '{w.name}' needs check (a shell command) and done_when (a regex on its output)")
    try:
        re.compile(w.done_when)
    except re.error as e:
        raise ManifestError(f"watch '{w.name}': done_when is not a valid regex: {e}") from e
    if seconds(w.every) < 10:
        raise ManifestError(f"watch '{w.name}': every must be at least 10s")
    seconds(w.timeout)
    _model(w.model, f"watch '{w.name}'")
    for a in w.args:
        _name(a, f"watch '{w.name}' arg")
    return w


def _parse_routine(i: int, raw) -> Routine:
    from bondi.cron import CronError, parse_cron
    t = _table(raw, f"routines[{i}]")
    r = Routine(name=_name(t.get("name", ""), f"routines[{i}].name"), schedule=str(t.get("schedule", "")).strip(),
                description=str(t.get("description", "")), runner=str(t.get("runner", "local")),
                model=str(t.get("model", "sonnet")), prompt=str(t.get("prompt", "")).strip(),
                prompt_file=str(t.get("prompt_file", "")), tools=_strs(t.get("tools"), f"routines[{i}].tools"),
                cwd=str(t.get("cwd", "")), notify=bool(t.get("notify", True)), timeout=str(t.get("timeout", "30m")))
    seconds(r.timeout)
    try:
        parse_cron(r.schedule)
    except CronError as e:
        raise ManifestError(f"routine '{r.name}': {e}") from e
    if r.runner not in RUNNERS:
        raise ManifestError(f"routine '{r.name}': runner must be one of {', '.join(sorted(RUNNERS))}")
    _model(r.model, f"routine '{r.name}'")
    if bool(r.prompt) == bool(r.prompt_file):
        raise ManifestError(f"routine '{r.name}': set exactly one of prompt or prompt_file")
    if r.prompt_file:
        r.prompt_file = _relative(r.prompt_file, f"routine '{r.name}' prompt_file")
    return r


def parse(text: str) -> Manifest:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"invalid TOML: {e}") from e
    claude = raw.get("claude", {}) or {}
    homes = raw.get("home", {}) or {}
    m = Manifest(
        name=_name(raw.get("name", ""), "name"),
        description=str(raw.get("description", "")),
        skills=_strs(claude.get("skills"), "claude.skills"),
        files=[_relative(f, "claude.files") for f in _strs(claude.get("files"), "claude.files")],
        dirs=[_relative(d, "claude.dirs") for d in _strs(claude.get("dirs"), "claude.dirs")],
        home_files=[_relative(f, "home.files") for f in _strs(homes.get("files"), "home.files")],
        agent_memory=raw.get("agent_memory", True),
    )
    if not isinstance(m.agent_memory, bool):
        raise ManifestError("agent_memory must be true or false")
    keys: set[str] = set()
    for i, p in enumerate(raw.get("projects", []) or []):
        t = _table(p, f"projects[{i}]")
        if "repo" in t or "remote" in t:
            raise ManifestError(f"projects[{i}]: repo and remote were replaced by github = \"owner/name\"")
        from bondi.projects import normalize
        github = normalize(str(t.get("github", "")))
        if not github:
            raise ManifestError(f"projects[{i}].github is required, like \"acme/app\"")
        key = _name(t.get("key") or project_key(github), f"projects[{i}].key")
        if key in keys:
            raise ManifestError(f"two projects share the key '{key}'; set key = explicitly")
        keys.add(key)
        m.projects.append(Project(key=key, github=github, memory=bool(t.get("memory", True))))
    m.lead = _parse_lead(raw.get("lead"))
    m.roles = [_parse_role(i, r) for i, r in enumerate(raw.get("roles", []) or [])]
    for key in ("watches", "routines"):
        if key in raw:
            raise ManifestError(f"[[{key}]] belongs in {AUTOMATIONS}, next to {MANIFEST}")
    _unique(m.roles, "roles")
    if m.roles and not m.lead:
        raise ManifestError("roles need a [lead]: bondi runs one lead session that hands work to the roles")
    if m.lead and any(r.name == m.lead.name for r in m.roles):
        raise ManifestError(f"the lead and a role are both named '{m.lead.name}'")
    for s in m.skills:
        if "/" in s or s in ("", ".", ".."):
            raise ManifestError(f"claude.skills: '{s}' must be a skill folder name or a glob like 'review-*'")
    return m


def parse_automations(text: str, where: str = AUTOMATIONS) -> tuple[list[Watch], list[Routine]]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"{where}: invalid TOML: {e}") from e
    extra = set(raw) - {"watches", "routines"}
    if extra:
        raise ManifestError(f"{where} holds only [[watches]] and [[routines]]; found {', '.join(sorted(extra))}")
    watches = [_parse_watch(i, w) for i, w in enumerate(raw.get("watches", []) or [])]
    routines = [_parse_routine(i, r) for i, r in enumerate(raw.get("routines", []) or [])]
    _unique(watches, "watches")
    _unique(routines, "routines")
    return watches, routines


def load(path: Path) -> Manifest:
    """Load bondi.toml, plus automations.toml from the same folder when it exists."""
    if not path.is_file():
        raise ManifestError(f"no {MANIFEST} at {path}")
    m = parse(path.read_text())
    auto = path.with_name(AUTOMATIONS)
    if auto.is_file():
        m.watches, m.routines = parse_automations(auto.read_text(), str(auto))
    return m


def _v(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value)


def _list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[\n" + "".join(f"  {json.dumps(i)},\n" for i in items) + "]"


def _block(header: str, obj, fields: list[tuple[str, object]]) -> list[str]:
    out = ["", header]
    for key, default in fields:
        value = getattr(obj, key)
        if default is None or value != default:
            out.append(f"{key} = {_v(value)}")
    return out


def dump(m: Manifest) -> str:
    out = [
        f"name = {_v(m.name)}",
        f"description = {_v(m.description)}",
        *([] if m.agent_memory else ["agent_memory = false"]),
        "",
        "[claude]",
        f"skills = {_list(m.skills)}",
        f"files = {_list(m.files)}",
        f"dirs = {_list(m.dirs)}",
        "",
        "[home]",
        f"files = {_list(m.home_files)}",
    ]
    for p in m.projects:
        out += _block("[[projects]]", p, [("github", None), ("key", None), ("memory", True)])
    if m.lead:
        out += _block("[lead]", m.lead, [("name", None), ("model", None), ("usage", None), ("playbook", ""), ("parallel", True)])
    for r in m.roles:
        out += _block("[[roles]]", r, [("name", None), ("model", None), ("usage", None), ("tools", []), ("skills", []),
                                       ("effort", ""), ("prompt", ""), ("prompt_file", "")])
    return "\n".join(out) + "\n"


def dump_automations(watches: list[Watch], routines: list[Routine]) -> str:
    out: list[str] = []
    for w in watches:
        out += _block("[[watches]]", w, [("name", None), ("description", ""), ("args", []), ("check", None),
                                         ("done_when", None), ("every", "2m"), ("timeout", "24h"), ("notify", True),
                                         ("model", "haiku"), ("then", ""), ("tools", []), ("cwd", "")])
    for r in routines:
        out += _block("[[routines]]", r, [("name", None), ("description", ""), ("schedule", None), ("runner", "local"),
                                          ("model", "sonnet"), ("prompt", ""), ("prompt_file", ""), ("tools", []), ("cwd", ""),
                                          ("notify", True), ("timeout", "30m")])
    return "\n".join(out).lstrip("\n") + "\n"
