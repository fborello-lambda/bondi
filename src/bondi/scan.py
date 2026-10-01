"""Inventory one Claude folder so a person or the bondi-create skill can pick what a bondi carries."""

import os
import re
from pathlib import Path

from bondi import layout, state
from bondi import projects as projects_mod
from bondi.entries import roots
from bondi.manifest import load, MANIFEST

CLAUDE_FILES = ["CLAUDE.md", "settings.json", "keybindings.json", "statusline.sh"]
CLAUDE_DIRS = ["memory", "rules", "agents", "commands", "hooks", "output-styles"]
HOME_FILES = [".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".gitconfig", ".gitignore_global", ".ssh/config", ".config/gh/config.yml"]


def _short(text: str, limit: int = 64) -> str:
    text = " ".join(text.split()).strip('"')
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."


def _description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return ""
    m = re.search(r"^description:\s*(>-?|\|)?\s*(.*?)(?=^\w[\w-]*:|^---)", text, re.S | re.M)
    if m:
        return _short(m.group(2))
    # No frontmatter: use the first line of prose.
    body = re.sub(r"\A---.*?^---\s*", "", text, flags=re.S | re.M)
    first = next((ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "<!--"))), "")
    return _short(first)


def _hardcoded_home(p: Path) -> bool:
    h = str(layout.home()).encode()
    files = [p] if p.is_file() else [Path(d) / f for d, _, fs in os.walk(p) for f in fs][:200]
    for f in files:
        try:
            if h in f.read_bytes():
                return True
        except OSError:
            pass
    return False


def owners(cdir: Path) -> dict[str, str]:
    """Live path -> name of the installed bondi that already carries it in this Claude folder."""
    out = {}
    for inst in state.load():
        if inst.cdir.resolve() != cdir.resolve() or not (inst.repo / MANIFEST).exists():
            continue
        m = load(inst.repo / MANIFEST)
        for r in roots(m, inst.cdir, inst.repo, inst.base):
            out[str(r.live)] = inst.name
    return out


def inventory(cdir: Path) -> dict:
    own = owners(cdir)
    from bondi import bundle
    managed = layout.third_party_skills()
    shipped = bundle.skill_names()
    skills = []
    sdir = cdir / "skills"
    for e in sorted(os.scandir(sdir), key=lambda e: e.name) if sdir.is_dir() else []:
        p = Path(e.path)
        link = p.is_symlink()
        target = layout.collapse(os.readlink(p)) if link else ""
        skills.append({
            "name": e.name,
            "kind": "link" if link else "folder",
            "target": target,
            "target_exists": p.exists(),
            "third_party": e.name in managed or "/.agents/skills/" in os.path.realpath(p) or e.name in shipped,
            "bundled": e.name in shipped,
            "description": _description(p / "SKILL.md"),
            "hardcoded_home": (not link) and _hardcoded_home(p),
            "owned_by": own.get(str(p), ""),
        })
    projects = []
    pdir = cdir / "projects"
    for e in sorted(os.scandir(pdir), key=lambda e: e.name) if pdir.is_dir() else []:
        mem = Path(e.path) / "memory"
        files = [f for f in mem.rglob("*") if f.is_file()] if mem.is_dir() and not mem.is_symlink() else []
        if not files:
            continue
        repo = layout.decode_project(e.name)
        root = projects_mod.main_root(repo) if repo else None
        projects.append({
            "folder": e.name,
            "repo": layout.collapse(repo) if repo else "",
            "root": str(root) if root else "",
            "github": (projects_mod.github_of(root) or "") if root else "",
            "memory_files": len(files),
            "hardcoded_home": _hardcoded_home(mem),
            "owned_by": own.get(str(mem), ""),
        })

    def entry(p: Path, rel: str) -> dict:
        count = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 1
        return {"path": rel, "files": count, "hardcoded_home": _hardcoded_home(p), "owned_by": own.get(str(p), "")}

    return {
        "claude_dir": layout.collapse(cdir),
        "other_claude_dirs": [layout.collapse(d) for d in layout.detect_claude_dirs() if d.resolve() != cdir.resolve()],
        "skills": skills,
        "files": [entry(cdir / f, f) for f in CLAUDE_FILES if (cdir / f).is_file()],
        "dirs": [entry(cdir / d, d) for d in CLAUDE_DIRS if (cdir / d).is_dir()],
        "projects": projects,
        "home_files": [entry(layout.home() / f, f) for f in HOME_FILES if (layout.home() / f).is_file()],
    }
