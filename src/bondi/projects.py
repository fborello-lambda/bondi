"""Find where a GitHub repo lives on this machine and keep one memory folder for it."""

import filecmp
import functools
import os
import re
import shutil
from pathlib import Path

from bondi import gitops, layout, ui
from bondi.manifest import Project

GITHUB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
_URL_RE = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$")


def normalize(url: str) -> str | None:
    s = url.strip()
    hit = _URL_RE.match(s)
    s = hit.group(1) if hit else s
    return s if GITHUB_RE.match(s) and s.split("/")[1] not in (".", "..") else None


def main_root(path: Path) -> Path | None:
    """The main checkout of the repo at path. Claude keys memory by it, so worktrees share it."""
    return _main_root(str(path)) if path.is_dir() else None


def github_of(root: Path) -> str | None:
    return _github_of(str(root)) if root.is_dir() else None


# Each Claude project folder costs two git calls; one command asks about the same folders many times.
@functools.lru_cache(maxsize=None)
def _main_root(path: str) -> Path | None:
    r = gitops.git(Path(path), "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    if r.returncode:
        return None
    common = Path(r.stdout.decode().strip())
    return common.parent if common.name == ".git" else None


@functools.lru_cache(maxsize=None)
def _github_of(root: str) -> str | None:
    url = gitops.remote_url(Path(root))
    return normalize(url) if url else None


def clear_cache() -> None:
    _main_root.cache_clear()
    _github_of.cache_clear()
    layout.decode_project.cache_clear()


def memory_dir(cdir: Path, root: Path) -> Path:
    return cdir / "projects" / layout.encode_project(root) / "memory"


def _files(d: Path) -> list[Path]:
    return [f for f in d.rglob("*") if f.is_file()] if d.is_dir() else []


def _folders(cdir: Path) -> list[tuple[str, Path, Path]]:
    """(github, main checkout, memory folder) for every Claude project folder that belongs to a GitHub repo."""
    out = []
    pdir = cdir / "projects"
    for e in sorted(os.scandir(pdir), key=lambda e: e.name) if pdir.is_dir() else []:
        path = layout.decode_project(e.name)
        root = main_root(path) if path else None
        gh = github_of(root) if root else None
        if gh:
            out.append((gh.lower(), root, Path(e.path) / "memory"))
    return out


def clones(cdir: Path) -> dict[str, list[Path]]:
    """Every GitHub repo that has a Claude project folder here, with each main checkout of it."""
    out: dict[str, list[Path]] = {}
    seen: set[Path] = set()
    for gh, root, _ in _folders(cdir):
        if root.resolve() not in seen:
            seen.add(root.resolve())
            out.setdefault(gh, []).append(root)
    return out


def _clone_target(p: Project) -> Path:
    return layout.home() / "code" / p.github.split("/")[1]


def choose_home(cdir: Path, p: Project, current: str | None, ask: bool = False, dry_run: bool = False) -> Path | None:
    """The checkout whose memory folder holds this project's memory on this machine."""
    cands = clones(cdir).get(p.github.lower(), [])
    if current and not ask:
        cur = main_root(layout.expand(current))
        if cur and (github_of(cur) or "").lower() == p.github.lower():
            return cur
    if len(cands) == 1 and not ask:
        return cands[0]
    if cands and (dry_run or not ui.interactive()):
        return max(cands, key=lambda c: len(_files(memory_dir(cdir, c))))
    if cands:
        choices = [ui.Choice(layout.collapse(c), str(c), hint=f"{len(_files(memory_dir(cdir, c)))} memory file(s)")
                   for c in cands] + [ui.Choice("Another folder", "")]
        picked = ui.select(f"Which checkout of {p.github} holds its memory here? The others link to it",
                           choices, default=str(layout.expand(current)) if current else str(cands[0]))
        if picked:
            return Path(picked)
    if dry_run:
        return None
    if not ui.interactive():
        ui.hint(f"{p.github} has no checkout here yet. Run bondi sync in a terminal to say where it is.")
        return None
    target = _clone_target(p)
    answer = ui.text(f"Where is {p.github} cloned on this machine? Leave empty to clone it to {layout.collapse(target)}", "")
    if answer:
        root = main_root(layout.expand(answer))
        if not root or (github_of(root) or "").lower() != p.github.lower():
            ui.warn(f"{answer} is not a checkout of {p.github}. Skipped; run bondi sync --ask to try again.")
            return None
        return root
    if target.exists():
        ui.warn(f"{layout.collapse(target)} already exists. Skipped; run bondi sync --ask to pick a folder.")
        return None
    gitops.clone(f"https://github.com/{p.github}.git", target)
    ui.done(f"Cloned {p.github} to {layout.collapse(target)}")
    return target


def fill(cdir: Path, root: Path, profile_memory: Path, fresh: bool) -> list[str]:
    """Copy profile files a new home lacks, so the next sync does not read them as deleted here.

    In a fresh home, a file that already differs from the profile is returned as a conflict: it must
    not overwrite the profile just because this machine wrote it first."""
    live = memory_dir(cdir, root)
    if live.is_symlink():
        target = Path(os.path.realpath(live))
        live.unlink()
        shutil.copytree(target, live) if target.is_dir() else live.mkdir(parents=True)
    conflicts = []
    for f in _files(profile_memory):
        rel = f.relative_to(profile_memory)
        dest = live / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        elif fresh and not filecmp.cmp(f, dest, shallow=False):
            conflicts.append(str(rel))
    return conflicts


def _conflict_name(dest: Path, root: Path) -> Path:
    slug = layout.encode_project(root).strip("-")[-60:]
    return dest.with_name(f"{dest.stem}.from-{slug}{dest.suffix}.bondi-conflict")


def link_others(cdir: Path, home: Path, github: str, bdir: Path, prefer: Path | None = None) -> list[str]:
    """Merge other checkouts' memory into the home folder, then link them to it. One memory per repo.

    On a differing file the home's copy wins, unless the other checkout is `prefer` (the previous home)."""
    home_mem = memory_dir(cdir, home)
    home_mem.mkdir(parents=True, exist_ok=True)
    notes = []
    # Other checkouts, and other spellings of a checkout's path (a symlink in it), each have a folder.
    for gh, root, mem in _folders(cdir):
        if gh != github.lower() or os.path.realpath(mem) == os.path.realpath(home_mem):
            continue
        if mem.is_symlink():
            mem.unlink()
        elif mem.is_dir():
            wins = prefer is not None and root.resolve() == prefer.resolve()
            for f in _files(mem):
                dest = home_mem / f.relative_to(mem)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                elif not filecmp.cmp(f, dest, shallow=False):
                    loser = _conflict_name(dest, home if wins else root)
                    shutil.copy2(dest if wins else f, loser)
                    if wins:
                        shutil.copy2(f, dest)
                    notes.append(f"{layout.collapse(dest)} differed between checkouts; the other version is "
                                 f"{loser.name}. Keep what you need, then delete it")
            shutil.copytree(mem, bdir / layout.collapse(mem).lstrip("~").lstrip("/"), dirs_exist_ok=True)
            shutil.rmtree(mem)
            notes.append(f"merged {layout.collapse(root)}'s memory into {layout.collapse(home)}")
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.symlink_to(home_mem, target_is_directory=True)
    return notes


def strays(cdir: Path) -> list[Path]:
    """Memory folders whose checkout is gone. Claude never loads them again."""
    pdir = cdir / "projects"
    out = []
    for e in sorted(os.scandir(pdir), key=lambda e: e.name) if pdir.is_dir() else []:
        mem = Path(e.path) / "memory"
        if not mem.is_symlink() and _files(mem) and not layout.decode_project(e.name):
            out.append(mem)
    return out


def scattered(cdir: Path) -> dict[str, list[Path]]:
    """Repos with memory in more than one real folder."""
    real: dict[str, list[Path]] = {}
    for gh, _, mem in _folders(cdir):
        if not mem.is_symlink() and _files(mem):
            real.setdefault(gh, []).append(mem)
    return {gh: mems for gh, mems in real.items() if len(mems) > 1}
