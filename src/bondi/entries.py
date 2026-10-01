"""Map a manifest to concrete (checkout path, live path) pairs on this machine."""

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from bondi import gitops, guard, layout
from bondi.manifest import Manifest

LINK_SUFFIX = ".link"


@dataclass(frozen=True)
class Root:
    rel: str
    live: Path
    is_dir: bool


@dataclass(frozen=True)
class Item:
    rel: str
    live: Path
    link: bool = False
    # The live side holds the other kind (link vs folder), so bondi must not write it.
    shadow: bool = False


def _match(name: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


def _generated_skills(m: Manifest, cdir: Path) -> set[str]:
    """Skill folders bondi writes itself (lead and schedules skills). They are output, never carried as files."""
    from bondi import bundle, generated, state, team
    names = team.generated_skill_names(m) | bundle.skill_names()
    skills = str(cdir / "skills") + "/"
    keys = set(generated.orphans())
    for inst in state.load():
        keys |= set(inst.generated)
    for key in keys:
        path = str(layout.expand(key))
        if path.startswith(skills):
            names.add(path[len(skills):].split("/")[0])
    return names


def _score(name: str, patterns: list[str]) -> int:
    """How strongly patterns claim a skill: an exact name beats any glob; a longer fixed part beats a shorter one."""
    best = -1
    for pat in patterns:
        if pat == name:
            return 1000
        if fnmatch.fnmatchcase(name, pat):
            best = max(best, len(pat) - sum(pat.count(c) for c in "*?[]"))
    return best


def _others(m: Manifest, cdir: Path) -> list[tuple[int, list[str], set[str]]]:
    """Other profiles in this Claude folder: (install order, skill patterns, skills already carried)."""
    from bondi import state
    from bondi.manifest import MANIFEST, load
    out = []
    for order, inst in enumerate(state.load()):
        if inst.name == m.name or inst.cdir.resolve() != cdir.resolve() or not (inst.repo / MANIFEST).is_file():
            continue
        repo = inst.repo / "claude" / "skills"
        carried = {e.name.removesuffix(LINK_SUFFIX) for e in os.scandir(repo)} if repo.is_dir() else set()
        try:
            patterns = load(inst.repo / MANIFEST).skills
        except Exception:
            patterns = []
        out.append((order, patterns, carried))
    return out


def _mine(m: Manifest, cdir: Path, name: str, others, my_order: int) -> bool:
    """When two profiles' globs match one skill, the one that carries it, or names it most precisely, keeps it."""
    mine = _score(name, m.skills)
    for order, patterns, carried in others:
        if mine == 1000:
            return True
        if name in carried:
            return False
        theirs = _score(name, patterns)
        if theirs > mine or (theirs == mine and order < my_order):
            return False
    return True


def skill_names(m: Manifest, cdir: Path, checkout: Path, base: str | None) -> list[str]:
    names: set[str] = set()
    live = cdir / "skills"
    third_party = layout.third_party_skills()
    if live.is_dir():
        names |= {e.name for e in os.scandir(live) if _match(e.name, m.skills) and e.name not in third_party}
    repo = checkout / "claude" / "skills"
    if repo.is_dir():
        names |= {e.name.removesuffix(LINK_SUFFIX) for e in os.scandir(repo)}
    if base:
        for p in gitops.ls_tree(checkout, base, "claude/skills"):
            names.add(p.split("/")[2].removesuffix(LINK_SUFFIX))
    from bondi import state
    skip = _generated_skills(m, cdir)
    others = _others(m, cdir)
    order = next((i for i, inst in enumerate(state.load()) if inst.name == m.name and inst.cdir.resolve() == cdir.resolve()), 10**6)
    in_repo = {e.name.removesuffix(LINK_SUFFIX) for e in os.scandir(repo)} if repo.is_dir() else set()
    return sorted(n for n in names if _match(n, m.skills) and n not in skip
                  and (n in in_repo or _mine(m, cdir, n, others, order)))


def saved_homes(m: Manifest, cdir: Path) -> dict[str, str]:
    from bondi import state
    return next((i.projects for i in state.load() if i.name == m.name and i.cdir.resolve() == cdir.resolve()), {})


def roots(m: Manifest, cdir: Path, checkout: Path, base: str | None = None,
          homes: dict[str, str] | None = None) -> list[Root]:
    from bondi.projects import memory_dir
    homes = saved_homes(m, cdir) if homes is None else homes
    out = [Root(f"claude/{f}", cdir / f, False) for f in m.files]
    out += [Root(f"claude/{d}", cdir / d, True) for d in m.dirs]
    # A project with no checkout here yet has no root, so sync leaves its files alone.
    out += [Root(f"projects/{p.key}/memory", memory_dir(cdir, layout.expand(homes[p.key])), True)
            for p in m.projects if p.memory and p.key in homes]
    out += [Root(f"home/{f}", layout.home() / f, False) for f in m.home_files]
    out += [Root(f"claude/skills/{n}", cdir / "skills" / n, True) for n in skill_names(m, cdir, checkout, base)]
    return out


def _walk(top: Path) -> list[str]:
    if not top.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames if d not in guard.SKIP_NAMES]
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), top).replace(os.sep, "/")
            if not guard.skipped(rel):
                found.append(rel)
    return found


def _dir_items(root: Root, checkout: Path, base: str | None, shadow: bool) -> list[Item]:
    rels = set() if shadow else set(_walk(root.live))
    rels |= set(_walk(checkout / root.rel))
    if base:
        prefix = root.rel + "/"
        rels |= {p[len(prefix):] for p in gitops.ls_tree(checkout, base, root.rel) if p.startswith(prefix)}
    return [Item(f"{root.rel}/{r}", root.live / r, shadow=shadow) for r in sorted(rels) if not guard.skipped(r)]


def items(m: Manifest, cdir: Path, checkout: Path, base: str | None, homes: dict[str, str] | None = None) -> list[Item]:
    out: list[Item] = []
    for root in roots(m, cdir, checkout, base, homes):
        if not root.is_dir:
            out.append(Item(root.rel, root.live))
            continue
        if not root.rel.startswith("claude/skills/"):
            out.extend(_dir_items(root, checkout, base, shadow=False))
            continue
        link_rel = root.rel + LINK_SUFFIX
        in_repo = (checkout / link_rel).exists() or (base is not None and gitops.show(checkout, base, link_rel) is not None)
        if root.live.is_symlink():
            kind = "link"
        elif root.live.is_dir():
            kind = "dir"
        else:
            kind = "link" if (checkout / link_rel).exists() else "dir"
        if kind == "link" or in_repo:
            out.append(Item(link_rel, root.live, link=True, shadow=kind != "link"))
        out.extend(_dir_items(root, checkout, base, shadow=kind == "link"))
    return out
