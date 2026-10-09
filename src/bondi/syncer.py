"""Three-way sync per file: the live file, the checkout copy, and the copy at the last sync (base)."""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from bondi import gitops, guard, layout
from bondi.entries import Item, items
from bondi.manifest import Manifest

TO_LIVE, TO_REPO, DEL_LIVE, DEL_REPO, CONFLICT, BLOCKED = "to_live", "to_repo", "del_live", "del_repo", "conflict", "blocked"


@dataclass
class Action:
    op: str
    item: Item
    live: bytes | None
    repo: bytes | None
    note: str = ""


@dataclass
class Result:
    applied: dict[str, int] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def read_live(it: Item) -> bytes | None:
    p = it.live
    if it.shadow:
        return None
    if it.link:
        return layout.collapse(os.readlink(p)).encode() if p.is_symlink() else None
    if not p.is_file():
        return None
    data = p.read_bytes()
    return guard.strip_session_keys(data) if guard.is_memory(it.rel) else data


def read_repo(it: Item, checkout: Path) -> bytes | None:
    p = checkout / it.rel
    if not p.is_file():
        return None
    data = p.read_bytes()
    return data.strip() if it.link else data


def read_base(it: Item, checkout: Path, base: str) -> bytes | None:
    data = gitops.show(checkout, base, it.rel)
    return data.strip() if (data is not None and it.link) else data


def plan(m: Manifest, cdir: Path, checkout: Path, base: str | None, conflicts: list[str],
         first_install: bool = True, deferred: dict[str, str] | None = None,
         homes: dict[str, str] | None = None) -> list[Action]:
    actions = []
    pending = set(conflicts)
    deferred = deferred or {}
    for it in items(m, cdir, checkout, base, homes):
        l, r = read_live(it), read_repo(it, checkout)
        if l == r:
            continue
        note = ""
        if it.rel in deferred and deferred[it.rel] == DEL_LIVE and r is None and l is not None:
            op = DEL_LIVE
        elif it.rel in deferred and deferred[it.rel] == DEL_REPO and l is None and r is not None:
            op = DEL_REPO
        elif it.rel in pending:
            op = CONFLICT
        elif base is None and l is not None and r is not None and not first_install:
            # The last-sync commit is gone (history rewritten): never guess which side wins.
            op, note = CONFLICT, "changed on both sides; the last sync is no longer in the profile's history"
        elif base is None:
            op = TO_REPO if r is None else TO_LIVE
            note = "replace, backup kept" if (l is not None and r is not None) else ""
        else:
            b = read_base(it, checkout, base)
            if l == b:
                op = TO_LIVE if r is not None else DEL_LIVE
            elif r == b:
                op = TO_REPO if l is not None else DEL_REPO
            else:
                op = CONFLICT
        if it.shadow and op in (TO_LIVE, DEL_LIVE):
            op, note = CONFLICT, "a folder here and a link in the profile, or the reverse: choose with --keep-local or --keep-remote"
        if op == TO_REPO and l is not None and not it.link:
            hit = guard.find_secret(l)
            if hit:
                op, note = BLOCKED, f"looks like a credential ({hit})"
        actions.append(Action(op, it, l, r, note))
    return actions


def _backup(p: Path, backup_dir: Path) -> None:
    if not (p.exists() or p.is_symlink()):
        return
    rel = layout.collapse(p).lstrip("~").lstrip("/")
    dest = backup_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        dest.with_name(dest.name + ".link").write_text(os.readlink(p))
        if p.is_file():
            shutil.copy2(os.path.realpath(p), dest)
    elif p.is_dir():
        shutil.copytree(p, dest, symlinks=True, dirs_exist_ok=True)
    elif p.is_file():
        shutil.copy2(p, dest)


def _write_live(a: Action, checkout: Path) -> None:
    p = a.item.live
    p.parent.mkdir(parents=True, exist_ok=True)
    if a.item.link:
        if p.is_symlink() or p.is_file():
            p.unlink()
        os.symlink(layout.expand(a.repo.decode()), p)
        return
    target = Path(os.path.realpath(p)) if p.is_symlink() else p
    tmp = target.with_name(target.name + ".bondi-tmp")
    tmp.write_bytes(a.repo)
    shutil.copymode(checkout / a.item.rel, tmp)
    os.replace(tmp, target)


def _write_repo(a: Action, checkout: Path) -> None:
    dest = checkout / a.item.rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if a.item.link:
        dest.write_bytes(a.live + b"\n")
    elif guard.is_memory(a.item.rel):
        dest.write_bytes(a.live)
    else:
        shutil.copy2(a.item.live, dest)


def _del_repo(a: Action, checkout: Path) -> None:
    p = checkout / a.item.rel
    if p.exists():
        p.unlink()
    parent = p.parent
    while parent != checkout and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _skill_root(it: Item) -> Path:
    """The live skill folder (or link) an item belongs to: <claude>/skills/<name>."""
    depth = len(Path(it.rel.removesuffix(".link")).parts) - 3
    return it.live.parents[depth - 1] if depth > 0 else it.live


def _clear_other_kind(it: Item, backup_dir: Path) -> None:
    """Make room for the profile's kind of skill: drop the local folder or link, after a backup."""
    root = _skill_root(it)
    if it.link and root.is_dir() and not root.is_symlink():
        _backup(root, backup_dir)
        shutil.rmtree(root)
    elif not it.link and root.is_symlink():
        _backup(root, backup_dir)
        root.unlink()


def apply(actions: list[Action], checkout: Path, backup_dir: Path, resolve: dict[str, str] | None = None) -> Result:
    res = Result()
    resolve = resolve or {}
    for a in actions:
        op = a.op
        choice = resolve.get(a.item.rel)
        if op == CONFLICT and choice == "local":
            op = TO_REPO if a.live is not None else DEL_REPO
        elif op == CONFLICT and choice == "remote":
            if a.item.shadow:
                _clear_other_kind(a.item, backup_dir)
            op = TO_LIVE if a.repo is not None else DEL_LIVE
        if op == TO_REPO and not a.item.link and a.live is not None and guard.find_secret(a.live):
            op = BLOCKED
        if op == TO_LIVE:
            _backup(a.item.live, backup_dir)
            _write_live(a, checkout)
        elif op == DEL_LIVE:
            if a.item.live.exists() or a.item.live.is_symlink():
                _backup(a.item.live, backup_dir)
                a.item.live.unlink()
        elif op == TO_REPO:
            _write_repo(a, checkout)
        elif op == DEL_REPO:
            _del_repo(a, checkout)
        elif op == CONFLICT:
            if a.repo is not None and not a.item.link and not a.item.shadow:
                side = a.item.live.with_name(a.item.live.name + ".bondi-conflict")
                side.parent.mkdir(parents=True, exist_ok=True)
                side.write_bytes(a.repo)
            res.conflicts.append(a.item.rel)
            continue
        elif op == BLOCKED:
            res.blocked.append(a.item.rel)
            continue
        res.applied[op] = res.applied.get(op, 0) + 1
    return res
