"""Files bondi writes from bondi.toml. Ownership lives in local state, so the files carry no bondi markers."""

import hashlib
from pathlib import Path

from bondi import layout
from bondi.state import Install
from bondi.syncer import _backup


def orphans_file() -> Path:
    from bondi import state
    return state.routines_file().with_name("orphans.json")


def remember_orphans(inst: Install) -> None:
    """Keep generated files that outlive their profile from being carried by another profile."""
    from bondi import state
    if inst.generated:
        known = set(state.read_json(orphans_file(), []))
        state.write_json(orphans_file(), sorted(known | set(inst.generated)))


def orphans() -> set[str]:
    from bondi import state
    return set(state.read_json(orphans_file(), []))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def apply(inst: Install, wanted: dict[Path, str], backup_dir: Path, dry_run: bool = False) -> list[str]:
    """Write wanted files and remove stale ones; never touch a file bondi did not write or that was hand-edited."""
    notes = []
    owned = dict(inst.generated)
    for path, text in wanted.items():
        key = layout.collapse(path)
        current = path.read_text(errors="replace") if path.is_file() else None
        if current == text:
            owned[key] = sha(text)
            continue
        if current is not None and owned.get(key) != sha(current):
            why = "edited by hand; delete it to let bondi regenerate it" if key in owned else "not written by bondi"
            notes.append(f"kept {key}: {why}")
            continue
        notes.append(f"{'would write' if dry_run else 'wrote'} {key}")
        if not dry_run:
            _backup(path, backup_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if path.suffix == ".sh":
                path.chmod(0o755)
            owned[key] = sha(text)
    for key in sorted(set(owned) - {layout.collapse(p) for p in wanted}):
        path = layout.expand(key)
        if not path.is_file():
            owned.pop(key)
            continue
        if sha(path.read_text(errors="replace")) != owned[key]:
            notes.append(f"kept {key}: no longer generated, but edited by hand")
            owned.pop(key)
            continue
        notes.append(f"{'would remove' if dry_run else 'removed'} {key}")
        if not dry_run:
            _backup(path, backup_dir)
            path.unlink()
            parent = path.parent
            while parent.name not in ("agents", "skills") and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            owned.pop(key)
    if not dry_run:
        inst.generated = owned
    return notes
