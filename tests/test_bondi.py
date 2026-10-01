import os

from conftest import make_clone, personal_manifest, seed_claude, tracked, work_manifest


def checkout(m, name):
    return m.home / ".config/bondi/profiles" / name


def test_new_carries_only_listed_files(machines, remote):
    a = machines("alice")
    seed_claude(a)
    assert a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y") == 0
    files = tracked(checkout(a, "personal"))
    assert {"bondi.toml", "README.md", "claude/CLAUDE.md", "claude/settings.json",
            "claude/memory/rules.md", "claude/skills/pr-review/SKILL.md"} <= files
    assert not any("claude.json" in f or f.endswith(".jsonl") or "deploy" in f for f in files)


def test_install_on_second_machine_with_other_home(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    b.write(".claude/settings.json", '{"model": "old"}\n')
    assert b.run("add", remote, "-y") == 0
    assert (b.claude / "memory/rules.md").read_text() == "- no em dashes\n"
    assert (b.claude / "settings.json").read_text() == '{"model": "opus"}\n'
    backups = list((b.home / ".local/share/bondi/backups").rglob("settings.json"))
    assert backups and backups[0].read_text() == '{"model": "old"}\n'


def test_project_memory_follows_the_github_repo_to_any_path(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    assert "projects/app/memory/MEMORY.md" in tracked(checkout(a, "work"))
    make_clone(b, "src/other-name")
    b.run("add", remote, "-y")
    assert (b.project_memory("src/other-name") / "MEMORY.md").read_text() == "- app fact\n"


def test_sync_round_trip_add_edit_delete(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    b.write(".claude/memory/new-fact.md", "- learned on bob\n")
    (b.claude / "memory/rules.md").write_text("- no em dashes\n- short sentences\n")
    assert b.run("sync", "-y") == 0
    assert a.run("sync", "-y") == 0
    assert (a.claude / "memory/new-fact.md").read_text() == "- learned on bob\n"
    assert "short sentences" in (a.claude / "memory/rules.md").read_text()
    (a.claude / "memory/new-fact.md").unlink()
    a.run("sync", "-y")
    b.run("sync", "-y")
    assert not (b.claude / "memory/new-fact.md").exists()


def test_conflict_is_kept_until_resolved(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    (a.claude / "memory/rules.md").write_text("alice version\n")
    a.run("sync", "-y")
    (b.claude / "memory/rules.md").write_text("bob version\n")
    b.run("sync", "-y")
    assert (b.claude / "memory/rules.md").read_text() == "bob version\n"
    assert (b.claude / "memory/rules.md.bondi-conflict").read_text() == "alice version\n"
    b.run("sync", "-y")
    assert (b.claude / "memory/rules.md").read_text() == "bob version\n", "a conflict must never resolve itself"
    b.run("sync", "--keep-local", "-y")
    a.run("sync", "-y")
    assert (a.claude / "memory/rules.md").read_text() == "bob version\n"
    assert not (b.claude / "memory/rules.md.bondi-conflict").exists()


def test_secret_is_never_committed(machines, remote):
    a = machines("alice")
    seed_claude(a)
    a.write(".claude/memory/leak.md", "token ghp_" + "a" * 36 + "\n")
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    assert "claude/memory/leak.md" not in tracked(checkout(a, "personal"))


def test_two_claude_folders_need_an_explicit_choice(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "--remote", remote, "-y")
    b.write(".claude/settings.json", "{}\n")
    b.write(".claude-second/settings.json", '{"model": "second"}\n')
    assert b.run("add", remote, "-y") == 1
    assert not (b.claude / "memory").exists()
    assert b.run("add", remote, "--claude-dir", "~/.claude-second", "-y") == 0
    assert (b.home / ".claude-second/memory/rules.md").exists()
    assert not (b.claude / "memory").exists(), "must not touch the Claude folder that was not chosen"
    assert (b.claude / "settings.json").read_text() == "{}\n"


def test_overlapping_bondis_are_refused(machines):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(personal_manifest(a)), "-y")
    clash = a.write("clash.toml", 'name = "clash"\n[claude]\ndirs = ["memory"]\n')
    assert a.run("export", "--from", str(clash), "-y") == 1


def test_third_party_skills_are_never_carried(machines):
    a = machines("alice")
    seed_claude(a)
    a.write(".agents/skills/vendor-tool/SKILL.md", "---\nname: vendor-tool\n---\n")
    a.write(".claude/skills/vendor-tool/SKILL.md", "---\nname: vendor-tool\n---\n")
    everything = a.write("all.toml", 'name = "all"\n[claude]\nskills = ["*"]\n')
    a.run("export", "--from", str(everything), "-y")
    files = tracked(checkout(a, "all"))
    assert "claude/skills/pr-review/SKILL.md" in files
    assert not any("vendor-tool" in f for f in files)


def test_skill_links_travel_as_home_relative_targets(machines, remote):
    a, b = machines("alice"), machines("bob")
    for m in (a, b):
        m.write("code/shared-skills/improve/SKILL.md", "---\nname: improve\n---\n")
    a.claude.joinpath("skills").mkdir(parents=True)
    os.symlink(a.home / "code/shared-skills/improve", a.claude / "skills/improve")
    man = a.write("links.toml", 'name = "links"\n[claude]\nskills = ["improve"]\n')
    a.run("export", "--from", str(man), "--remote", remote, "-y")
    assert (checkout(a, "links") / "claude/skills/improve.link").read_text().strip() == "~/code/shared-skills/improve"
    b.run("add", remote, "-y")
    target = b.claude / "skills/improve"
    assert target.is_symlink() and os.readlink(target) == str(b.home / "code/shared-skills/improve")


def test_tidy_materializes_links_and_follows_nested_links(machines):
    a = machines("alice")
    old = a.home / "code/old-repo"
    (old / "shared").mkdir(parents=True)
    (old / "shared/rules.md").write_text("shared rule\n")
    (old / "review").mkdir()
    (old / "review/SKILL.md").write_text("---\nname: review\n---\n")
    os.symlink("../shared", old / "review/references")
    (a.claude / "skills").mkdir(parents=True)
    os.symlink(old / "review", a.claude / "skills/review")
    os.symlink(a.home / "gone", a.claude / "skills/dead")
    assert a.run("_tidy", "--links-into", "~/code/old-repo", "--do", "materialize", "--remove-broken", "-y") == 0
    skill = a.claude / "skills/review"
    assert skill.is_dir() and not skill.is_symlink()
    assert (skill / "references/rules.md").read_text() == "shared rule\n"
    assert not (skill / "references").is_symlink()
    assert not (a.claude / "skills/dead").is_symlink()
    assert list((a.home / ".local/share/bondi/backups").rglob("review.link"))


def test_denied_paths_are_rejected(machines):
    a = machines("alice")
    bad = a.write("bad.toml", 'name = "bad"\n[claude]\nfiles = [".claude.json"]\ndirs = ["projects"]\n[home]\nfiles = [".ssh/id_ed25519"]\n')
    assert a.run("export", "--from", str(bad), "-y") == 1


def test_a_remote_added_with_git_is_pushed_by_the_next_sync(machines, remote, capsys):
    import subprocess
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    assert a.run("export", "--from", str(personal_manifest(a)), "-y") == 0
    subprocess.run(["git", "-C", str(checkout(a, "personal")), "remote", "add", "origin", remote], check=True)
    assert a.run("sync", "-y") == 0
    assert "could not" not in capsys.readouterr().err
    assert b.run("add", remote, "-y") == 0
    assert (b.claude / "memory/rules.md").read_text() == "- no em dashes\n"
