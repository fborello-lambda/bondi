"""Thin wrappers over the git CLI. Auth is whatever git already uses (SSH agent, credential helper)."""

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(cwd: Path | None, *args: str, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, input=input, capture_output=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r


def out(cwd: Path, *args: str) -> str:
    return git(cwd, *args).stdout.decode().strip()


def check_url(url: str) -> str:
    if not url or url.startswith("-"):
        raise GitError(f"'{url}' is not a git URL")
    return url


def clone(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(None, "clone", "-q", "--", check_url(url), str(dest))


def init(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    git(dest, "init", "-q", "-b", "main")


def pushed(cwd: Path) -> bool:
    """True when the remote branch holds this checkout's latest commit."""
    up = git(cwd, "rev-parse", "-q", "--verify", "@{u}", check=False)
    return up.returncode == 0 and up.stdout.strip() == git(cwd, "rev-parse", "HEAD").stdout.strip()


def head(cwd: Path) -> str | None:
    r = git(cwd, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.decode().strip() or None


def remote_url(cwd: Path) -> str | None:
    r = git(cwd, "remote", "get-url", "origin", check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def set_remote(cwd: Path, url: str) -> None:
    check_url(url)
    if remote_url(cwd):
        git(cwd, "remote", "set-url", "origin", url)
    else:
        git(cwd, "remote", "add", "origin", url)


def has_commit(cwd: Path, sha: str | None) -> bool:
    return bool(sha) and git(cwd, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def recover(cwd: Path) -> None:
    """Abort a rebase or merge left behind by an earlier run."""
    gitdir = cwd / ".git"
    if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
        git(cwd, "rebase", "--abort", check=False)
    if (gitdir / "MERGE_HEAD").exists():
        git(cwd, "merge", "--abort", check=False)


def pull(cwd: Path) -> tuple[str | None, str | None]:
    """On a rebase conflict: keep local commits on a branch, reset to origin, return the common ancestor."""
    recover(cwd)
    if not remote_url(cwd) or not head(cwd):
        return None, None
    branch = out(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise GitError(f"{cwd} is on a detached HEAD; check out a branch there first")
    r = git(cwd, "ls-remote", "--exit-code", "--heads", "origin", branch, check=False)
    if r.returncode == 2:
        return None, None
    if r.returncode != 0:
        raise GitError(r.stderr.decode(errors="replace").strip() or "cannot reach origin")
    git(cwd, "fetch", "-q", "origin", branch)
    remote_ref = f"origin/{branch}"
    if git(cwd, "rebase", "-q", remote_ref, check=False).returncode == 0:
        return None, None
    recover(cwd)
    ancestor = out(cwd, "merge-base", "HEAD", remote_ref)
    kept = f"bondi-local-{out(cwd, 'rev-parse', '--short', 'HEAD')}"
    git(cwd, "branch", "-f", kept, "HEAD")
    git(cwd, "reset", "-q", "--hard", remote_ref)
    return ancestor, kept


def commit_all(cwd: Path, message: str) -> bool:
    git(cwd, "add", "-A")
    if git(cwd, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git(cwd, "commit", "-q", "-m", message)
    return True


def push(cwd: Path) -> None:
    if remote_url(cwd):
        git(cwd, "push", "-q", "-u", "origin", "HEAD")


def show(cwd: Path, sha: str, path: str) -> bytes | None:
    r = git(cwd, "show", f"{sha}:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def ls_tree(cwd: Path, sha: str, prefix: str) -> list[str]:
    r = git(cwd, "ls-tree", "-r", "--name-only", "-z", sha, "--", prefix, check=False)
    return [p for p in r.stdout.decode().split("\0") if p] if r.returncode == 0 else []
