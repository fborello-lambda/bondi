"""Where bondi and Claude Code keep things on this machine."""

import functools
import json
import os
import re
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _xdg(var: str, default: str) -> Path:
    # The XDG spec says to ignore a relative value.
    v = os.environ.get(var, "")
    return Path(v) if os.path.isabs(v) else home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "bondi"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "bondi"


def checkout_dir(name: str) -> Path:
    return config_dir() / "profiles" / name


def local_automations() -> Path:
    return config_dir() / "automations.toml"


def backup_root() -> Path:
    return data_dir() / "backups"


def state_file() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "bondi" / "installs.json"


def expand(p: str) -> Path:
    if p == "~" or p.startswith("~/"):
        return home() / p[2:] if p != "~" else home()
    return Path(p)


def collapse(p: str | Path) -> str:
    s = str(p)
    for h in dict.fromkeys([str(home()), os.path.realpath(home())]):
        if s == h:
            return "~"
        if s.startswith(h + "/"):
            return "~" + s[len(h):]
    return s


def default_claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return expand(env) if env else home() / ".claude"


def _looks_like_claude_dir(d: Path) -> bool:
    return d.is_dir() and any((d / n).exists() for n in ("settings.json", ".claude.json", "projects"))


def detect_claude_dirs() -> list[Path]:
    found: list[Path] = []
    candidates = [home() / ".claude", *sorted(home().glob(".claude-*"))]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        candidates.insert(0, expand(env))
    for c in candidates:
        if _looks_like_claude_dir(c) and c.resolve() not in {f.resolve() for f in found}:
            found.append(c)
    return found


LONG = 200


def _java_hash(text: str) -> int:
    data = text.encode("utf-16-le")
    h = 0
    for i in range(0, len(data), 2):
        h = (h * 31 + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def _base36(n: int) -> str:
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def _dashes(text: str) -> str:
    # Claude works on UTF-16 units, so a character outside the BMP becomes two dashes.
    return "".join(c if c.isascii() and c.isalnum() else "--" if ord(c) > 0xFFFF else "-" for c in text)


def encode_project(repo: Path) -> str:
    """Claude Code's ~/.claude/projects/<folder> name: non-alphanumerics become '-', long names are cut and hashed."""
    text = str(repo)
    e = _dashes(text)
    return e if len(e) <= LONG else f"{e[:LONG]}-{_base36(abs(_java_hash(text)))}"


@functools.lru_cache(maxsize=4096)
def decode_project(folder: str, limit: int = 200_000) -> Path | None:
    """Find the real path whose encoded form is `folder`, walking the filesystem one segment at a time."""
    budget = [limit]
    long_name = len(folder) > LONG and re.match(rf"^.{{{LONG}}}-[0-9a-z]+$", folder) is not None
    wanted = folder[1:LONG] if long_name else folder[1:]

    def deeper(base: Path, depth: int) -> Path | None:
        # Past the cut, only the hash tells paths apart: try each folder until one encodes to `folder`.
        if encode_project(base) == folder:
            return base
        if depth > 12:
            return None
        try:
            children = [Path(c.path) for c in os.scandir(base) if c.is_dir()]
        except OSError:
            return None
        for child in children:
            budget[0] -= 1
            if budget[0] < 0:
                return None
            hit = deeper(child, depth + 1)
            if hit:
                return hit
        return None

    def walk(base: Path, rest: str) -> Path | None:
        if not rest:
            return deeper(base, 0) if long_name else base
        try:
            children = list(os.scandir(base))
        except OSError:
            return None
        for child in children:
            budget[0] -= 1
            if budget[0] < 0:
                return None
            enc = _dashes(child.name)
            if long_name and enc.startswith(rest):
                hit = deeper(Path(child.path), 0)
                if hit:
                    return hit
            if rest == enc:
                if not long_name:
                    return Path(child.path)
                hit = deeper(Path(child.path), 0)
                if hit:
                    return hit
            # Follow links: a checkout often sits behind one. The budget bounds a link loop.
            if rest.startswith(enc + "-") and child.is_dir():
                hit = walk(Path(child.path), rest[len(enc) + 1:])
                if hit:
                    return hit
        return None

    if not folder.startswith("-"):
        return None
    return walk(Path("/"), wanted)


def third_party_skills() -> set[str]:
    """Skill names that `npx skills` installed (its lock file and ~/.agents/skills). bondi never carries them."""
    names: set[str] = set()
    agents = home() / ".agents"
    try:
        names |= set(json.loads((agents / ".skill-lock.json").read_text()).get("skills", {}))
    except (OSError, ValueError):
        pass
    if (agents / "skills").is_dir():
        names |= {e.name for e in os.scandir(agents / "skills")}
    return names
