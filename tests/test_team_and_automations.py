import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from bondi import automations
from bondi.manifest import ManifestError, parse, parse_automations
from conftest import seed_claude, tracked

TEAM = """
name = "work"
[claude]
skills = ["*"]
[lead]
name = "work-lead"
model = "opus"
usage = "Plan the work, hand it to roles, verify."
playbook = "lead/playbook.md"
parallel = false
[[roles]]
name = "reviewer"
model = "sonnet"
usage = "Reviews a diff. Never edits code."
tools = ["Read", "Grep"]
skills = ["pr-review"]
prompt_file = "roles/reviewer.md"
[[roles]]
name = "implementer"
model = "sonnet"
usage = "Implements one task with tests."
"""


def checkout(m, name):
    return m.home / ".config/bondi/profiles" / name


def team_profile(m):
    toml = m.write("src/bondi.toml", TEAM)
    m.write("src/lead/playbook.md", "House rule: verify before you report.\n")
    m.write("src/roles/reviewer.md", "Review the diff at the head. Report findings with file:line.\n")
    return toml


def fake_exe(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_team_installs_lead_skill_and_role_agents(machines, remote):
    a, b = machines("alice"), machines("bob")
    seed_claude(a)
    assert a.run("export", "--from", str(team_profile(a)), "--remote", remote, "-y") == 0
    files = tracked(checkout(a, "work"))
    assert {"lead/playbook.md", "roles/reviewer.md"} <= files
    assert not any(f.startswith("claude/skills/work-lead") for f in files), "the lead skill is output, never carried"

    b.write(".claude/agents/mine.md", "---\nname: mine\ndescription: my own\n---\n")
    assert b.run("add", remote, "-y") == 0
    reviewer = (b.claude / "agents/reviewer.md").read_text()
    assert "model: sonnet" in reviewer and 'skills: ["pr-review"]' in reviewer and "tools: Read, Grep" in reviewer
    assert "Report findings with file:line." in reviewer
    lead = (b.claude / "skills/work-lead/SKILL.md").read_text()
    assert "name: work-lead" in lead and "| `reviewer` | sonnet |" in lead and "one task at a time" in lead.lower()
    assert (b.claude / "skills/work-lead/playbook.md").read_text().startswith("House rule")
    assert "bondi" not in reviewer, "generated files carry no bondi metadata"
    assert (b.claude / "agents/mine.md").read_text().startswith("---\nname: mine"), "never touch your own agents"


def test_renamed_role_replaces_its_agent_but_keeps_hand_edits(machines, remote):
    a, b = machines("alice"), machines("bob")
    a.run("export", "--from", str(team_profile(a)), "--remote", remote, "-y")
    b.run("add", remote, "-y")
    (b.claude / "agents/implementer.md").write_text("my edits\n")
    toml = checkout(a, "work") / "bondi.toml"
    toml.write_text(toml.read_text().replace('name = "reviewer"', 'name = "checker"').replace('name = "implementer"', 'name = "builder"'))
    a.run("sync", "-y")
    b.run("sync", "-y")
    assert not (b.claude / "agents/reviewer.md").exists() and (b.claude / "agents/checker.md").exists()
    assert (b.claude / "agents/implementer.md").read_text() == "my edits\n", "a hand-edited file is left alone"


def test_automations_live_in_their_own_file():
    with pytest.raises(ManifestError, match="automations.toml"):
        parse('name = "p"\n[[watches]]\nname = "w"\n')
    with pytest.raises(ManifestError, match="10s"):
        parse_automations('[[watches]]\nname = "w"\ncheck = "true"\ndone_when = "x"\nevery = "1s"\n')
    with pytest.raises(ManifestError, match="5 fields"):
        parse_automations('[[routines]]\nname = "r"\nschedule = "* *"\nprompt = "hi"\n')


WATCH = """
[[watches]]
name = "state-is"
args = ["file"]
check = "cat {file}"
done_when = "MERGED|CLOSED"
every = "10s"
model = "haiku"
then = "It is {output}. Say done."
"""


def test_watch_polls_until_done_then_runs_follow_up_and_notifies(machines, tmp_path, monkeypatch):
    a = machines("alice")
    a.write(".config/bondi/automations.toml", WATCH)
    flag = a.write("pr-state", "OPEN\n")
    notes = tmp_path / "notes.txt"
    monkeypatch.setenv("BONDI_CLAUDE", str(fake_exe(tmp_path, "claude", 'echo "follow-up: $* | $(cat)"')))
    monkeypatch.setenv("BONDI_NOTIFY_CMD", str(fake_exe(tmp_path, "notify", f'echo "$1 | $2" >> {notes}')))
    monkeypatch.setenv("HOME", str(a.home))
    source, w = automations.find_watch("state-is")
    run = automations.prepare(source, w, [str(flag)])
    automations.state.watches_dir().mkdir(parents=True)
    done = automations.poll(run, sleep=lambda s: flag.write_text("MERGED\n"))
    assert done.status == "done" and done.checks == 2 and done.last_output == "MERGED"
    assert "--model haiku" in done.follow_up and "It is MERGED. Say done." in done.follow_up
    assert "state-is" in notes.read_text() and "done" in notes.read_text()


def test_watch_arguments_are_shell_quoted(machines, tmp_path, monkeypatch):
    a = machines("alice")
    a.write(".config/bondi/automations.toml", WATCH.replace('then = "It is {output}. Say done."\n', ""))
    monkeypatch.setenv("HOME", str(a.home))
    monkeypatch.setenv("BONDI_NOTIFY_CMD", "true")
    pwned = tmp_path / "pwned"
    source, w = automations.find_watch("state-is")
    run = automations.prepare(source, w, [f"x; touch {pwned}; echo MERGED"])
    automations.state.watches_dir().mkdir(parents=True)
    subprocess.run(run.check, shell=True, capture_output=True)
    assert not pwned.exists(), "an argument must never run as a command"


ROUTINES = """
[[routines]]
name = "hello"
schedule = "* * * * *"
model = "haiku"
prompt = "Say hello."
[[routines]]
name = "weekly"
schedule = "0 9 * * 1"
runner = "desktop"
prompt = "Weekly summary."
"""


def test_tick_runs_a_due_local_routine_once_per_minute(machines, remote, tmp_path, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    a.write("src/bondi.toml", 'name = "personal"\n[claude]\ndirs = ["memory"]\n')
    a.write("src/automations.toml", ROUTINES)
    a.run("export", "--from", str(a.home / "src/bondi.toml"), "-y")
    out = tmp_path / "ran.txt"
    monkeypatch.setenv("BONDI_CLAUDE", str(fake_exe(tmp_path, "claude", f'echo "$* | $(cat)" >> {out}')))
    now = datetime(2026, 1, 5, 10, 17)
    assert automations.tick(now) == ["personal/hello@~/.claude"]
    assert automations.tick(now) == [], "the same minute never runs twice"
    for _ in range(50):
        if out.exists():
            break
        time.sleep(0.1)
    assert "--model haiku" in out.read_text() and "Say hello." in out.read_text()
    sched = (a.claude / "skills/personal-schedules/SKILL.md").read_text()
    assert "## weekly" in sched and "`0 9 * * 1`" in sched and "Weekly summary." in sched and "## hello" not in sched


def test_export_writes_a_draft_and_opens_the_editor(machines, script, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    editor = a.write("edit.sh", "#!/bin/sh\nsed -i.bak '/deploy-check/d' \"$1\"\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    s = script([True])                        # create
    assert a.run("export", "personal") == 0
    assert any("Create profile" in q for q in s.asked)
    co = checkout(a, "personal")
    text = (co / "bondi.toml").read_text()
    assert '"pr-review"' in text and "deploy-check" not in text and 'github = "acme/app"' in text
    assert {"claude/CLAUDE.md", "claude/memory/rules.md", "claude/skills/pr-review/SKILL.md"} <= tracked(co)
    assert not (a.home / ".local/share/bondi/drafts/personal").exists()


def test_export_keeps_the_draft_when_you_give_up(machines, script, monkeypatch):
    a = machines("alice")
    seed_claude(a)
    editor = a.write("break.sh", "#!/bin/sh\nprintf 'name = 1\\n' > \"$1\"\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    script([False])                           # open it again?
    assert a.run("export", "work") == 1
    assert (a.home / ".local/share/bondi/drafts/work/bondi.toml").is_file()
    assert not checkout(a, "work").exists()
