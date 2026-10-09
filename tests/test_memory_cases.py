"""Project memory in the cases that lose data when handled carelessly."""

import json
import shutil

from bondi import layout
from conftest import make_clone, seed_claude, tracked, work_manifest


def checkout(m, name):
    return m.home / ".config/bondi/profiles" / name


def profile_memory(m):
    return (checkout(m, "work") / "projects/app/memory/MEMORY.md").read_text()


def test_a_new_homes_own_memory_never_overwrites_the_profile(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    make_clone(b, "src/app")
    b.write(str(b.project_memory("src/app").relative_to(b.home) / "MEMORY.md"), "- b's first fact\n")
    b.run("sync", "-y")
    a.run("sync", "-y")
    assert (a.project_memory("code/app") / "MEMORY.md").read_text() == "- app fact\n"
    assert profile_memory(a) == "- app fact\n"


def test_a_claude_folder_outside_home_works(machines, remote, tmp_path):
    a = machines("alice")
    cdir = tmp_path / "volume/claude"
    (cdir / "memory").mkdir(parents=True)
    (cdir / "memory/rules.md").write_text("- rule\n")
    (cdir / "settings.json").write_text("{}\n")
    for rel, text in (("code/app", "- one\n"), ("disk2/app", "- two\n")):
        make_clone(a, rel)
        mem = cdir / "projects" / layout.encode_project(a.home / rel) / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text(text)
    assert a.run("export", "--from", str(work_manifest(a)), "--claude-dir", str(cdir), "--remote", remote, "-y") == 0
    assert a.run("sync", "--claude-dir", str(cdir), "-y") == 0


def test_a_deleted_home_checkout_does_not_crash_or_delete(machines, remote):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    shutil.rmtree(a.home / "code/app")
    assert a.run("sync", "-y") == 0
    assert "projects/app/memory/MEMORY.md" in tracked(checkout(a, "work"))


def test_moving_the_home_keeps_an_edit_that_was_not_synced(machines, remote, script):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    (a.project_memory("code/app") / "MEMORY.md").write_text("- edited\n")
    make_clone(a, "disk2/app")
    script(["~/disk2/app", True])
    assert a.run("sync", "--ask") == 0
    assert (a.project_memory("disk2/app") / "MEMORY.md").read_text() == "- edited\n"
    assert a.project_memory("code/app").is_symlink()
    assert profile_memory(a) == "- edited\n"


def test_two_profiles_cannot_carry_one_repo(machines, remote):
    a = machines("alice")
    seed_claude(a)
    assert a.run("export", "--from", str(work_manifest(a)), "-y") == 0
    other = a.write("other.toml", 'name = "other"\n[[projects]]\ngithub = "ACME/app"\n')
    assert a.run("export", "--from", str(other), "-y") == 1


def test_add_dry_run_writes_nothing(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    make_clone(b, "code/app")
    assert b.run("add", remote, "--dry-run", "-y") == 0
    assert not (b.project_memory("code/app") / "MEMORY.md").exists()


def test_three_checkouts_keep_every_differing_version(machines, remote):
    a = machines("alice")
    seed_claude(a)
    for rel, text in (("disk2/app", "- two\n"), ("disk3/app", "- three\n")):
        make_clone(a, rel)
        a.write(str(a.project_memory(rel).relative_to(a.home) / "MEMORY.md"), text)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    homes = [r for r in ("code/app", "disk2/app", "disk3/app") if not a.project_memory(r).is_symlink()]
    assert len(homes) == 1
    losers = list(a.project_memory(homes[0]).glob("*.bondi-conflict"))
    assert len(losers) == 2 and len({p.name for p in losers}) == 2


def test_settings_json_keeps_its_text_and_odd_shapes_do_not_crash(machines, remote):
    a = machines("alice")
    seed_claude(a)
    (a.claude / "settings.json").write_text('{"note": "café → ok"}\n')
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    assert "café → ok" in (a.claude / "settings.json").read_text()
    (a.claude / "settings.json").write_text('{"hooks": {"SubagentStart": {}}}\n')
    assert a.run("sync", "-y") == 0
    assert json.loads((a.claude / "settings.json").read_text()) == {"hooks": {"SubagentStart": {}}}


STAMPED = """---
name: ci-runs
description: Rerun only the failed jobs
metadata:
  node_type: memory
  type: feedback
  originSessionId: 0f8e2c1a-5b7d-4e3a-9c6f-2d1b0a9e8f7c
  modified: 2026-10-01T01:20:15.266Z
---

Rerun with --failed.
"""


def test_session_keys_never_reach_the_profile(machines, remote):
    import subprocess
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    mem = a.project_memory("code/app")
    (mem / "ci-runs.md").write_text(STAMPED)
    a.run("export", "--from", str(work_manifest(a)), "--remote", remote, "-y")
    carried = (checkout(a, "work") / "projects/app/memory/ci-runs.md").read_text()
    assert "originSessionId" not in carried and "node_type" not in carried and "modified" not in carried
    assert "metadata:\n  type: feedback\n---\n\nRerun with --failed.\n" in carried
    head = lambda: subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout(a, "work"),
                                  capture_output=True, text=True).stdout
    before = head()
    (mem / "ci-runs.md").write_text(STAMPED.replace("2026-10-01T01:20:15.266Z", "2026-10-09T10:00:00.000Z"))
    a.run("sync", "-y")
    assert head() == before, "a new session stamp alone is not an edit"
    b.run("add", remote, "-y")
    make_clone(b, "code/app")
    b.run("sync", "-y")
    assert (b.project_memory("code/app") / "ci-runs.md").read_text() == carried


def test_stripping_leaves_other_frontmatter_alone():
    from bondi.guard import strip_session_keys
    only_stamps = b"---\nname: x\nmetadata:\n  originSessionId: abc\n  modified: now\n---\nbody\n"
    assert strip_session_keys(only_stamps) == b"---\nname: x\n---\nbody\n"
    untouched = b"---\nname: x\nmodifiedBy: me\n---\nmodified: in the body stays\n"
    assert strip_session_keys(untouched) == untouched
    assert strip_session_keys(b"no frontmatter\nmodified: x\n") == b"no frontmatter\nmodified: x\n"
