import os
import subprocess
from pathlib import Path

import pytest

from bondi import layout, ui
from bondi.cli import main


class Machine:
    """One fake machine: its own $HOME, run bondi as if logged in there."""

    def __init__(self, root: Path, name: str, monkeypatch):
        self.home = root / "Users" / name
        self.home.mkdir(parents=True)
        self.claude = self.home / ".claude"
        self._mp = monkeypatch

    def run(self, *argv: str) -> int:
        self._mp.setenv("HOME", str(self.home))
        for var in ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            self._mp.delenv(var, raising=False)
        return main(list(argv))

    def write(self, rel: str, text: str) -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def project_memory(self, repo: str) -> Path:
        return self.claude / "projects" / layout.encode_project(self.home / repo) / "memory"


class Script:
    """Scripted answers for the interactive prompts, matched by label."""

    def __init__(self, monkeypatch, answers: list):
        self.answers, self.asked = list(answers), []
        monkeypatch.setattr(ui, "interactive", lambda: not ui._assume_yes)
        monkeypatch.setattr(ui, "confirm", lambda msg, *a, **k: True if ui._assume_yes else self._pop(msg))
        monkeypatch.setattr(ui, "text", self._next)
        monkeypatch.setattr(ui, "select", self._select)
        monkeypatch.setattr(ui, "checkbox", self._checkbox)

    def _pop(self, msg):
        self.asked.append(msg)
        if os.environ.get('BONDI_DEBUG_SCRIPT'): print('ASK', repr(msg), '->', repr(self.answers[0] if self.answers else None))
        assert self.answers, f"no scripted answer left for: {msg}"
        return self.answers.pop(0)

    def _next(self, msg, *a, **k):
        return self._pop(msg)

    def _select(self, msg, choices, default=None):
        want = self._pop(msg)
        if want == "default":
            return default
        hit = [c.value for c in choices if c.label == want]
        assert hit, f"{msg}: no choice labelled {want!r} in {[c.label for c in choices]}"
        return hit[0]

    def _checkbox(self, msg, choices):
        want = self._pop(msg)
        missing = set(want) - {c.label for c in choices}
        assert not missing, f"{msg}: no choices {missing} in {[c.label for c in choices]}"
        return [c.value for c in choices if c.label in want]


@pytest.fixture(autouse=True)
def isolated_git(tmp_path, monkeypatch):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Bondi Test\n\temail = bondi@example.invalid\n[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture
def remote(tmp_path) -> str:
    path = tmp_path / "remote" / "profile.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return str(path)


@pytest.fixture
def machines(tmp_path, monkeypatch):
    return lambda name: Machine(tmp_path, name, monkeypatch)


@pytest.fixture
def script(monkeypatch):
    return lambda answers: Script(monkeypatch, answers)


def seed_claude(m: Machine) -> None:
    m.write(".claude/CLAUDE.md", "@memory/rules.md\n")
    m.write(".claude/settings.json", '{"model": "opus"}\n')
    m.write(".claude/memory/rules.md", "- no em dashes\n")
    m.write(".claude/skills/pr-review/SKILL.md", "---\nname: pr-review\ndescription: Review PRs.\n---\nbody\n")
    m.write(".claude/skills/deploy-check/SKILL.md", "---\nname: deploy-check\ndescription: Check a deploy.\n---\n")
    make_clone(m, "code/app")
    m.write(str(m.project_memory("code/app").relative_to(m.home) / "MEMORY.md"), "- app fact\n")
    # Decoys bondi must never carry.
    m.write(".claude/.claude.json", '{"oauthAccount": "secret"}\n')
    m.write(".claude/history.jsonl", "{}\n")
    m.write(str(m.project_memory("code/app").parent.relative_to(m.home) / "abc123.jsonl"), "{}\n")


def make_clone(m: Machine, rel: str, github: str = "acme/app") -> Path:
    """A checkout with a GitHub origin, plus the project folder Claude makes on its first session there."""
    path = m.home / rel
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", f"git@github.com:{github}.git"], check=True)
    m.project_memory(rel).parent.mkdir(parents=True, exist_ok=True)
    return path


def personal_manifest(m: Machine) -> Path:
    return m.write("personal.toml", """
name = "personal"
description = "Everything I want on every machine"
[claude]
skills = ["pr-review"]
files = ["CLAUDE.md", "settings.json"]
dirs = ["memory"]
""")


def work_manifest(m: Machine) -> Path:
    return m.write("work.toml", """
name = "work"
[claude]
skills = ["deploy-*"]
[[projects]]
github = "acme/app"
""")


def tracked(checkout: Path) -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=checkout, capture_output=True, text=True, check=True).stdout
    return set(out.split())


os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
