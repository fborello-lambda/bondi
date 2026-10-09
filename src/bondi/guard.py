"""Paths bondi never carries, files it skips, content it refuses to commit, and memory keys it drops."""

import re
from pathlib import PurePosixPath

from bondi.manifest import Manifest

# Machine-local Claude state: login, transcripts, caches. Project memory goes through [[projects]];
# skills go through [claude].skills, so third-party skills stay out.
DENY_CLAUDE = {
    ".claude.json", ".credentials.json", "history.jsonl", "settings.local.json", "projects", "skills",
    "sessions", "session-env", "shell-snapshots", "file-history", "cache", "telemetry", "debug",
    "paste-cache", "plugins", "backups", "statsig", "tasks", "todos", "stats-cache.json",
    "policy-limits.json", "remote-settings.json", "ide", "scheduled-tasks",
}
DENY_HOME = {
    ".claude", ".claude.json", ".netrc", ".npmrc", ".pypirc", ".aws", ".gnupg", ".local", ".ssh", ".docker",
    ".kube", ".git-credentials", ".pgpass", ".env", ".config/gh/hosts.yml", ".config/bondi", ".zsh_history", ".bash_history",
    ".python_history", ".node_repl_history", ".lesshst", ".viminfo",
}
ALLOW_HOME = {".ssh/config"}
SKIP_NAMES = {".ds_store", "__pycache__", ".git", "node_modules", ".venv"}
SKIP_SUFFIXES = (".bondi-conflict", ".bondi-tmp", ".pyc")
SECRET_NAMES = {".env", ".netrc", ".git-credentials", ".pgpass", "credentials.json", ".npmrc", ".pypirc"}

SECRET_RE = re.compile(
    rb"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|gh[ousr]_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}"
    rb"|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|[sr]k_live_[A-Za-z0-9]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    rb"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|lin_api_[A-Za-z0-9]{30,}"
    rb"|glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{36}|[a-z][a-z0-9+.-]*://[^\s/:@\"']+:[^\s/@\"']{6,}@"
    rb"|(?i:(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key|access[_-]?key)[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?)"
    rb"[A-Za-z0-9_\-./+=]{20,}"
)


def _low(p: str) -> str:
    return p.lower()


def _top(p: str) -> str:
    parts = PurePosixPath(p).parts
    return parts[0] if parts else ""


def is_key_file(p: str) -> bool:
    name = PurePosixPath(p).name.lower()
    return (name.startswith("id_") and not name.endswith(".pub")) or name.endswith((".pem", ".key", ".p12", ".pfx"))


def is_secret_file(p: str) -> bool:
    name = PurePosixPath(p).name.lower()
    return is_key_file(p) or name in SECRET_NAMES or name.startswith(".env.")


def _home_denied(p: str) -> bool:
    low = _low(p)
    if low in ALLOW_HOME:
        return False
    return any(low == d or low.startswith(d + "/") for d in DENY_HOME) or low.endswith("_history")


def manifest_errors(m: Manifest) -> list[str]:
    errs = []
    for p in m.files + m.dirs:
        if _low(_top(p)) == "skills":
            errs.append("carry skills with [claude].skills, not as a folder, so third-party skills stay out")
        elif _low(_top(p)) in DENY_CLAUDE or _low(p).endswith(".jsonl"):
            errs.append(f"claude path '{p}' is machine-local state and cannot be carried")
    for p in m.home_files:
        if _home_denied(p):
            errs.append(f"home path '{p}' is not allowed (credentials, history or Claude state)")
    if m.roles and "agents" in m.dirs:
        errs.append("roles write agents/<name>.md, so the same profile cannot also carry the agents folder")
    for p in m.files + m.dirs + m.home_files:
        if is_secret_file(p):
            errs.append(f"'{p}' looks like a key or credentials file")
    return sorted(set(errs))


def skipped(rel: str) -> bool:
    """Files inside carried folders that never travel: caches, conflict copies, keys and credentials."""
    parts = PurePosixPath(rel).parts
    return (any(p.lower() in SKIP_NAMES for p in parts) or rel.endswith(SKIP_SUFFIXES)
            or rel.endswith(".jsonl") or is_secret_file(rel))


# Frontmatter keys Claude Code stamps on a memory file. They name one session and one machine.
SESSION_KEYS = (b"originSessionId", b"node_type", b"modified")
_FRONT_RE = re.compile(rb"\A---\r?\n(.*?\r?\n)---(?:\r?\n|\Z)", re.S)
_SESSION_LINE_RE = re.compile(rb"^[ \t]*(?:" + b"|".join(SESSION_KEYS) + rb"):[^\n]*\n", re.M)
_EMPTY_METADATA_RE = re.compile(rb"^metadata:[ \t]*\r?\n(?![ \t])", re.M)


def is_memory(rel: str) -> bool:
    return "memory" in PurePosixPath(rel).parts and rel.endswith(".md")


def strip_session_keys(data: bytes) -> bytes:
    """Drop SESSION_KEYS from a leading frontmatter block; every other byte stays."""
    m = _FRONT_RE.match(data)
    if not m:
        return data
    front = _EMPTY_METADATA_RE.sub(b"", _SESSION_LINE_RE.sub(b"", m.group(1)))
    return data[:m.start(1)] + front + data[m.end(1):]


def find_secret(data: bytes) -> str | None:
    m = SECRET_RE.search(data)
    return m.group(0)[:12].decode(errors="replace") + "..." if m else None
