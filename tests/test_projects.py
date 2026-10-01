import json
import os
import subprocess

from bondi import layout, projects
from conftest import make_clone, seed_claude, tracked, work_manifest


def checkout(m, name):
    return m.home / ".config/bondi/profiles" / name


def test_two_checkouts_share_one_memory(machines, remote):
    a = machines("alice")
    seed_claude(a)
    make_clone(a, "disk2/app")
    a.write(str(a.project_memory("disk2/app").relative_to(a.home) / "MEMORY.md"), "- other fact\n")
    a.write(str(a.project_memory("disk2/app").relative_to(a.home) / "extra.md"), "extra\n")
    assert a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y") == 0
    home, other = a.project_memory("disk2/app"), a.project_memory("code/app")
    assert other.is_symlink() and other.resolve() == home.resolve()
    assert (home / "MEMORY.md").read_text() == "- other fact\n"
    loser = list(home.glob("MEMORY.from-*code-app.md.bondi-conflict"))
    assert len(loser) == 1 and loser[0].read_text() == "- app fact\n"
    assert {"projects/app/memory/MEMORY.md", "projects/app/memory/extra.md"} <= tracked(checkout(a, "work"))
    assert not any(p.endswith(".bondi-conflict") for p in tracked(checkout(a, "work")))


def test_worktrees_are_not_separate_checkouts(machines):
    a = machines("alice")
    app = make_clone(a, "code/app")
    subprocess.run(["git", "-C", str(app), "commit", "-q", "--allow-empty", "-m", "x"], check=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(a.home)})
    wt = a.home / "wt/app-x"
    subprocess.run(["git", "-C", str(app), "worktree", "add", "-q", "--detach", str(wt)], check=True)
    a.project_memory("wt/app-x").parent.mkdir(parents=True)
    assert projects.main_root(wt).resolve() == app.resolve()
    assert [r.resolve() for r in projects.clones(a.claude)["acme/app"]] == [app.resolve()]


def test_new_home_is_filled_not_read_as_deleted(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    make_clone(b, "later/app")
    assert b.run("sync", "-y") == 0
    assert (b.project_memory("later/app") / "MEMORY.md").read_text() == "- app fact\n"
    a.run("sync", "-y")
    assert "projects/app/memory/MEMORY.md" in tracked(checkout(a, "work"))


def test_agents_get_the_memory_index_and_bondi_toml_turns_it_off(machines, remote):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    settings = json.loads((a.claude / "settings.json").read_text())
    command = settings["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
    assert "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" in command and str(a.home) not in command
    script = a.claude / "skills/bondi-memory/scripts/agent_memory.sh"
    out = subprocess.run(["sh", str(script)], input=json.dumps({"cwd": str(a.home / "code/app")}),
                         capture_output=True, text=True, check=True).stdout
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "- app fact" in context and "memory folder is" in context
    toml = checkout(a, "work") / "bondi.toml"
    toml.write_text('agent_memory = false\n' + toml.read_text())
    a.run("sync", "-y")
    assert "hooks" not in json.loads((a.claude / "settings.json").read_text())


def test_memory_under_a_symlinked_path_is_merged_and_saved(machines, remote):
    a = machines("alice")
    make_clone(a, "code/app")
    (a.home / "linked").symlink_to(a.home / "code")
    alias = a.claude / "projects" / layout.encode_project(a.home / "linked/app") / "memory"
    alias.mkdir(parents=True)
    (alias / "MEMORY.md").write_text("- learned through the link\n")
    assert a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y") == 0
    home = a.project_memory("code/app")
    assert (home / "MEMORY.md").read_text() == "- learned through the link\n"
    assert alias.is_symlink() and alias.resolve() == home.resolve()
    assert "projects/app/memory/MEMORY.md" in tracked(checkout(a, "work"))
    assert projects.strays(a.claude) == [] and projects.scattered(a.claude) == {}
