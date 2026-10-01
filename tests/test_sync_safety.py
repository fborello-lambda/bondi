"""One test per finding from the adversarial review, so each stays fixed."""

import os
import shutil
import subprocess

from bondi.cli import main
from conftest import personal_manifest, seed_claude, tracked


def checkout(m, name):
    return m.home / ".config/bondi/profiles" / name


def two_machines(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    return a, b


def test_offline_edits_on_both_sides_become_a_conflict_not_markers(machines, remote):
    a, b = two_machines(machines, remote)
    co = checkout(b, "personal")
    subprocess.run(["git", "remote", "set-url", "origin", "/nonexistent"], cwd=co, check=True)
    (b.claude / "memory/rules.md").write_text("bob offline\n")
    b.run("sync", "-y")
    (a.claude / "memory/rules.md").write_text("alice online\n")
    a.run("sync", "-y")
    subprocess.run(["git", "remote", "set-url", "origin", remote], cwd=co, check=True)
    assert b.run("sync", "-y") == 1, "a remaining conflict makes sync exit non-zero"
    assert (b.claude / "memory/rules.md").read_text() == "bob offline\n"
    assert (b.claude / "memory/rules.md.bondi-conflict").read_text() == "alice online\n"
    status = subprocess.run(["git", "status", "--porcelain=2", "--branch"], cwd=co, capture_output=True, text=True).stdout
    assert "rebase" not in status and not (co / ".git/rebase-merge").exists()
    assert any(b.startswith("bondi-local-") for b in subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=co,
                                                                    capture_output=True, text=True).stdout.split())


def test_missing_claude_folder_deletes_nothing(machines, remote):
    a, b = two_machines(machines, remote)
    os.rename(b.claude, b.home / ".claude.moved")
    b.run("sync", "-y")
    assert "claude/memory/rules.md" in tracked(checkout(b, "personal"))


def test_mass_delete_needs_a_flag(machines, remote):
    a, b = two_machines(machines, remote)
    for i in range(8):
        a.write(f".claude/memory/m{i}.md", f"{i}\n")
    a.run("sync", "-y")
    b.run("sync", "-y")
    for f in (b.claude / "memory").iterdir():
        f.unlink()
    b.run("sync", "-y")
    assert "claude/memory/m3.md" in tracked(checkout(b, "personal")), "an emptied folder must not wipe the profile"
    b.run("sync", "-y", "--allow-mass-delete")
    assert "claude/memory/m3.md" not in tracked(checkout(b, "personal"))


def test_deletes_wait_for_consent_without_a_terminal(machines, remote):
    a, b = two_machines(machines, remote)
    (a.claude / "memory/rules.md").unlink()
    a.run("sync", "-y")
    b.run("sync")
    assert (b.claude / "memory/rules.md").exists(), "no -y and no terminal: nothing is deleted"
    b.run("sync", "-y")
    assert not (b.claude / "memory/rules.md").exists()


def test_keep_local_never_commits_a_secret(machines, remote):
    a, b = two_machines(machines, remote)
    (a.claude / "memory/rules.md").write_text("alice\n")
    a.run("sync", "-y")
    (b.claude / "memory/rules.md").write_text("token ghp_" + "b" * 36 + "\n")
    b.run("sync", "-y")
    b.run("sync", "--keep-local", "-y")
    assert b"ghp_" not in (checkout(b, "personal") / "claude/memory/rules.md").read_bytes()


def test_the_more_specific_glob_keeps_a_new_skill(machines):
    a = machines("alice")
    a.write(".claude/skills/notes/SKILL.md", "---\nname: notes\n---\n")
    a.write(".claude/skills/work-tool/SKILL.md", "---\nname: work-tool\n---\n")
    a.run("export", "--from", str(a.write("p.toml", 'name = "personal"\n[claude]\nskills = ["*"]\n')), "-y")
    a.run("export", "--from", str(a.write("w.toml", 'name = "work"\n[claude]\nskills = ["work-*"]\n')), "-y")
    a.write(".claude/skills/work-deploy/SKILL.md", "---\nname: work-deploy\n---\n")
    assert a.run("sync", "-y") == 0
    assert "claude/skills/work-deploy/SKILL.md" in tracked(checkout(a, "work"))
    assert "claude/skills/work-deploy/SKILL.md" not in tracked(checkout(a, "personal"))


def test_removed_profiles_generated_skill_is_not_swept_up(machines):
    a = machines("alice")
    a.write(".claude/skills/notes/SKILL.md", "---\nname: notes\n---\n")
    a.run("export", "--from", str(a.write("p.toml", 'name = "personal"\n[claude]\nskills = ["*"]\n')), "-y")
    a.run("export", "--from", str(a.write("w.toml", 'name = "work"\n[lead]\nname = "work-lead"\nusage = "Lead."\n')), "-y")
    shutil.rmtree(checkout(a, "work"))
    a.run("sync", "-y")
    assert not any("work-lead" in f for f in tracked(checkout(a, "personal")))


def test_xdg_dirs_move_profiles_and_state(machines, remote, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    xdg = a.home / "xdg"
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(var, str(xdg / sub))
    monkeypatch.setenv("HOME", str(a.home))
    assert main(["export", "--from", str(personal_manifest(a)), "--remote", remote, "-y"]) == 0
    assert (xdg / "cfg/bondi/profiles/personal/bondi.toml").is_file()
    assert (xdg / "state/bondi/installs.json").is_file()
    assert not (a.home / ".config/bondi").exists() and not (a.home / ".local").exists()
