"""Skills and scripts shipped inside bondi, kept current in every Claude folder bondi manages."""

import json
import shutil
import time
from importlib import resources
from pathlib import Path

from bondi import __version__, generated, layout, state, ui

ROOT = "bundled/skills"
HOOK_SCRIPT = "bondi-memory/scripts/agent_memory.sh"


def _walk(node, prefix: str = ""):
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            yield from _walk(child, rel + "/")
        elif not child.name.endswith((".pyc", ".DS_Store")):
            yield rel, child.read_text()


def files() -> dict[str, str]:
    """Bundled files, relative to a Claude folder's skills/ directory."""
    return dict(_walk(resources.files("bondi").joinpath(ROOT)))


def skill_names() -> set[str]:
    return {rel.split("/")[0] for rel in files()}


def _record() -> Path:
    return state.routines_file().with_name("bundled.json")


def ensure(cdir: Path, quiet: bool = False) -> list[str]:
    """Bring cdir's copy of the bundled skills to this bondi version. Returns what changed."""
    if not cdir.is_dir():
        return []
    data = state.read_json(_record(), {})
    key = layout.collapse(cdir)
    entry = data.get(key, {})
    pseudo = state.Install(name="bondi", claude_dir=key, checkout="", generated=entry.get("generated", {}))
    wanted = {cdir / "skills" / rel: text for rel, text in files().items()}
    notes = [n for n in generated.apply(pseudo, wanted, layout.backup_root() / "bundled") if not n.startswith("kept")]
    for n in generated.apply(pseudo, wanted, layout.backup_root() / "bundled", dry_run=True):
        if n.startswith("kept") and not quiet:
            ui.warn(f"bundled skill file {n}")
    data[key] = {**entry, "version": __version__, "generated": pseudo.generated}
    state.write_json(_record(), data)
    return notes


# One command for every machine and Claude folder, so a settings.json that a profile carries works everywhere.
HOOK_COMMAND = ('f="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/' + HOOK_SCRIPT + '"; [ ! -f "$f" ] || sh "$f"')


def _ours(entry) -> bool:
    return isinstance(entry, dict) and any(isinstance(h, dict) and HOOK_SCRIPT in str(h.get("command", ""))
                                           for h in entry.get("hooks", []) if isinstance(entry.get("hooks"), list))


def ensure_agent_hook(cdir: Path, wanted: bool) -> str | None:
    """Subagents skip project memory, so a SubagentStart hook hands them its index. bondi.toml turns it off."""
    settings = cdir / "settings.json"
    try:
        cfg = json.loads(settings.read_text()) if settings.is_file() else {}
    except (OSError, ValueError):
        return None
    hooks = cfg.get("hooks", {}) if isinstance(cfg, dict) else None
    events = hooks.get("SubagentStart", []) if isinstance(hooks, dict) else None
    if not isinstance(events, list):
        return f"left {layout.collapse(settings)} alone: its hooks are not in the shape bondi expects"
    keep = [e for e in events if not _ours(e)]
    mine = [{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}] if wanted else []
    if events == keep + mine:
        return None
    if settings.is_file():
        dest = layout.backup_root() / time.strftime("%Y%m%d-%H%M%S") / layout.collapse(settings).lstrip("~").lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(settings, dest)
    cfg.setdefault("hooks", {})["SubagentStart"] = keep + mine
    if not cfg["hooks"]["SubagentStart"]:
        del cfg["hooks"]["SubagentStart"]
        if not cfg["hooks"]:
            del cfg["hooks"]
    settings.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    if wanted:
        return f"Spawned agents in {layout.collapse(cdir)} now get the repo's memory index (a SubagentStart hook in settings.json)."
    return f"Removed bondi's SubagentStart hook from {layout.collapse(settings)} (agent_memory = false)."


def ensure_all(extra: list[Path] | None = None) -> None:
    """Refresh every Claude folder that has an installed profile, plus any extra ones."""
    from bondi.common import manifest_of
    from bondi.manifest import ManifestError
    found = state.load()
    cdirs = {i.cdir.resolve(): i.cdir for i in found}
    if not found and layout.default_claude_dir().is_dir():
        # A first-time user needs bondi-create before any profile exists.
        cdirs[layout.default_claude_dir().resolve()] = layout.default_claude_dir()
    for c in extra or []:
        cdirs.setdefault(c.resolve(), c)
    changed = 0
    for key, cdir in cdirs.items():
        changed += len(ensure(cdir, quiet=True))
        here = [i for i in found if i.cdir.resolve() == key and i.repo.exists()]
        if not cdir.is_dir() or not here:
            continue
        wanted = False
        try:
            for inst in here:
                m = manifest_of(inst)
                wanted |= bool(m.projects) and m.agent_memory
        except ManifestError:
            continue
        note = ensure_agent_hook(cdir, wanted)
        if note:
            ui.note(note)
    if changed:
        ui.done(f"Updated bondi's bundled skills ({changed} file change(s)) to version {__version__}.")
