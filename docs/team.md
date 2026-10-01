# Team: lead and roles

bondi supports one way of working with several agents: **one lead session, several roles.**

- The **lead** is your main session. It talks with you, plans the work, hands tasks to roles,
  verifies what they report, and reports back. It keeps the context.
- A **role** is a Claude Code subagent with its own model, tools and job. It does one task and
  reports. It never talks with you directly.

A profile with roles must have exactly one lead.

## Define a team

In `bondi.toml`:

```toml
[lead]
name = "work-lead"                # becomes the /work-lead skill
model = "opus"                    # the model the lead session should run
usage = "Start or resume orchestrated work: plan, hand off, verify, report."
playbook = "lead/playbook.md"     # optional: house rules, in the profile repo
parallel = false                  # one task at a time; true runs independent tasks in parallel

[[roles]]
name = "implementer"
model = "sonnet"
usage = "Implements one scoped task with tests and reports what changed."

[[roles]]
name = "reviewer"
model = "sonnet"
usage = "Reviews a diff for correctness and safety. Never edits code."
tools = ["Read", "Grep", "Glob", "Bash"]   # optional; default is every tool
skills = ["review-checklist"]              # optional; loaded into the role when it starts
prompt_file = "roles/reviewer.md"          # optional; or prompt = "..." inline
```

The `bondi-create` skill suggests a starter team (planner, implementer, reviewer) and asks for each model.

## What bondi generates

On `add` and `sync`, in the Claude folder the profile is installed into:

| File | What |
|---|---|
| `skills/<lead>/SKILL.md` | The lead skill: the team table, how to hand off work, how to verify, how to report. |
| `skills/<lead>/playbook.md` | A copy of your playbook, when you set one. |
| `agents/<role>.md` | One Claude Code subagent per role. |

These files are output. Edit `bondi.toml` and the prompt files instead (`bondi edit <profile>`).
bondi tracks what it wrote in its local state, so the files carry no bondi markers. It never
overwrites a file it did not write, and it leaves a file alone once you edit it by hand.

## Start a lead session

```bash
claude --model opus "/work-lead"
```

`bondi` alone prints the exact command for each profile. The session keeps Claude Code's full default
system prompt; the lead skill adds the way of working on top. The lead hands work to a role through
the Agent tool, and you can name a role yourself: `@agent-reviewer look at this diff`.

## Why a skill and not a main agent

Claude Code can run a subagent as the main session (`claude --agent <name>`), but then the agent's
prompt replaces the default system prompt. A short lead prompt would lose that guidance. A skill adds
the lead's way of working without taking anything away.
