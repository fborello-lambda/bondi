import os

from bondi import bundle
from conftest import seed_claude, tracked


def test_bundled_skills_arrive_stay_current_and_are_never_carried(machines, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(a.write("p.toml", 'name = "personal"\n[claude]\nskills = ["*"]\n')), "-y")
    notify = a.claude / "skills/bondi-notify/scripts/notify.sh"
    assert (a.claude / "skills/bondi-create/SKILL.md").is_file() and os.access(notify, os.X_OK)
    files = tracked(a.home / ".config/bondi/profiles/personal")
    assert not any("bondi-create" in f or "bondi-notify" in f for f in files), "bundled skills are never carried"

    # A newer bondi drops one file and changes another: the next run removes and updates them.
    shipped = bundle.files()
    newer = {k: v for k, v in shipped.items() if not k.startswith("bondi-create/")}
    newer["bondi-notify/SKILL.md"] = shipped["bondi-notify/SKILL.md"] + "\nNew guidance.\n"
    monkeypatch.setattr(bundle, "files", lambda: newer)
    a.run()
    assert not (a.claude / "skills/bondi-create").exists()
    assert (a.claude / "skills/bondi-notify/SKILL.md").read_text().endswith("New guidance.\n")


def test_a_hand_edited_bundled_file_is_left_alone(machines, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    a.run("export", "--from", str(a.write("p.toml", 'name = "personal"\n[claude]\ndirs = ["memory"]\n')), "-y")
    skill = a.claude / "skills/bondi-notify/SKILL.md"
    skill.write_text("my own version\n")
    shipped = bundle.files()
    monkeypatch.setattr(bundle, "files", lambda: {**shipped, "bondi-notify/SKILL.md": "newer\n"})
    a.run()
    assert skill.read_text() == "my own version\n"
