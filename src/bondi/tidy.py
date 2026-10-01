"""Housekeeping in a Claude folder before or after building a bondi: links, broken links, leftovers, home paths."""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bondi import layout
from bondi.syncer import _backup

TEXT_SUFFIXES = {".md", ".txt", ".toml", ".yaml", ".yml"}


@dataclass
class LinkGroup:
    source: Path
    links: list[Path] = field(default_factory=list)


@dataclass
class Findings:
    groups: list[LinkGroup] = field(default_factory=list)
    broken: list[Path] = field(default_factory=list)
    leftovers: list[Path] = field(default_factory=list)
    hardcoded: list[Path] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.groups or self.broken or self.leftovers or self.hardcoded)


def _source_of(target: Path) -> Path:
    r = subprocess.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else target.parent


def find(cdir: Path) -> Findings:
    f = Findings()
    groups: dict[Path, LinkGroup] = {}
    sdir = cdir / "skills"
    for p in sorted(sdir.iterdir()) if sdir.is_dir() else []:
        if not p.is_symlink():
            continue
        if not p.exists():
            f.broken.append(p)
            continue
        src = _source_of(Path(os.path.realpath(p)))
        groups.setdefault(src, LinkGroup(src)).links.append(p)
    f.groups = sorted(groups.values(), key=lambda g: str(g.source))
    home = str(layout.home()).encode() + b"/"
    tops = [cdir / "skills", cdir / "memory", cdir / "rules", cdir / "agents", *sorted(cdir.glob("projects/*/memory"))]
    # A linked project memory folder is another checkout's; its home is walked once.
    for top in (t for t in tops if t.is_dir() and not t.is_symlink()):
        # os.walk does not descend into symlinked folders, so linked skills are left to the link checks above.
        for dirpath, dirnames, files in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for name in files:
                p = Path(dirpath) / name
                if name.endswith(".bondi-conflict"):
                    f.leftovers.append(p)
                elif p.suffix in TEXT_SUFFIXES and not p.is_symlink() and home in p.read_bytes():
                    f.hardcoded.append(p)
    claude_md = cdir / "CLAUDE.md"
    if claude_md.is_file() and home in claude_md.read_bytes():
        f.hardcoded.append(claude_md)
    return f


IGNORE = {".git", "__pycache__", ".DS_Store", "node_modules"}


def _copy_deref(src: Path, dst: Path, stack: frozenset = frozenset()) -> None:
    # shutil.copytree checks relative nested links against the cwd, so resolve each link from its own folder.
    real_src = Path(os.path.realpath(src))
    if real_src in stack:
        return
    dst.mkdir(parents=True, exist_ok=True)
    for e in os.scandir(real_src):
        if e.name in IGNORE or e.name.endswith(".pyc"):
            continue
        real = Path(os.path.realpath(e.path))
        if not real.exists():
            continue
        if real.is_dir():
            _copy_deref(real, dst / e.name, stack | {real_src})
        else:
            shutil.copy2(real, dst / e.name)


def materialize(link: Path, backup_dir: Path) -> None:
    """Replace a skill link with a real copy of what it points to. Nested links are followed."""
    tmp = link.with_name(link.name + ".bondi-tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    _copy_deref(link, tmp)
    _backup(link, backup_dir)
    link.unlink()
    os.replace(tmp, link)


def remove_link(link: Path, backup_dir: Path) -> None:
    _backup(link, backup_dir)
    link.unlink()


def remove_file(p: Path, backup_dir: Path) -> None:
    _backup(p, backup_dir)
    p.unlink()


def home_lines(p: Path) -> list[str]:
    home = str(layout.home()) + "/"
    return [f"{i}: {line.strip()[:120]}" for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1) if home in line]


def rewrite_home(p: Path, backup_dir: Path) -> int:
    home = str(layout.home()) + "/"
    text = p.read_text()
    count = text.count(home)
    _backup(p, backup_dir)
    p.write_text(text.replace(home, "~/"))
    return count
