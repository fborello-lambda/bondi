"""Per-machine record of installed bondis. Never synced: each machine picks its own Claude folder."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bondi import layout


@dataclass
class Install:
    name: str
    claude_dir: str
    checkout: str
    base: str | None = None
    conflicts: list[str] = field(default_factory=list)
    # Deletes waiting for consent: path in the profile -> del_live or del_repo.
    deferred: dict[str, str] = field(default_factory=dict)
    # Files bondi generated (subagents, lead skill, schedules skill): path -> sha256 of what it wrote.
    generated: dict[str, str] = field(default_factory=dict)
    # Project key -> the checkout whose memory folder holds that project's memory here.
    projects: dict[str, str] = field(default_factory=dict)
    synced_at: float | None = None

    @property
    def cdir(self) -> Path:
        return layout.expand(self.claude_dir)

    @property
    def repo(self) -> Path:
        return layout.expand(self.checkout)

    @property
    def label(self) -> str:
        return f"{self.name} -> {self.claude_dir}"


def _dir() -> Path:
    return layout.state_file().parent


def load() -> list[Install]:
    f = layout.state_file()
    if not f.exists():
        return []
    known = set(Install.__dataclass_fields__)
    try:
        return [Install(**{k: v for k, v in i.items() if k in known}) for i in json.loads(f.read_text()).get("installs", [])]
    except (ValueError, TypeError, AttributeError) as e:
        from bondi.common import UserError
        raise UserError(f"{layout.collapse(f)} is damaged ({e}). Fix it, or move it away to start over") from e


def save(installs: list[Install]) -> None:
    write_json(layout.state_file(), {"installs": [asdict(i) for i in installs]})


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def find(installs: list[Install], name: str, claude_dir: Path | None = None) -> list[Install]:
    hits = [i for i in installs if i.name == name]
    if claude_dir is not None:
        hits = [i for i in hits if i.cdir.resolve() == claude_dir.resolve()]
    return hits


def watches_dir() -> Path:
    return _dir() / "watches"


def routines_file() -> Path:
    return _dir() / "routines.json"


def logs_dir() -> Path:
    return _dir() / "logs"
