# Reference

A profile repo holds `bondi.toml` and, optionally, `automations.toml`. Names are lowercase letters,
digits and `-`, at most 40 characters. Paths are relative to the folder they belong to; never absolute.

## bondi.toml

```toml
name = "work"                          # required
description = "Only on work machines"

agent_memory = true                    # optional: spawned agents get the repo's memory index

[claude]                               # relative to the Claude folder (~/.claude)
skills = ["deploy-check", "review-*"]  # skill folder names or globs
files = ["CLAUDE.md", "settings.json"]
dirs = ["memory", "hooks"]

[home]                                 # relative to ~
files = [".zshrc", ".gitconfig"]

[[projects]]                           # project memory: loads only in sessions inside the repo
github = "acme/app"                    # required: bondi finds the checkout by its origin, at any path
key = "app"                            # optional: folder name in the profile repo
memory = true

[lead]                                 # required when there are roles
name = "work-lead"
usage = "Start or resume orchestrated work."
model = "opus"
playbook = "lead/playbook.md"          # optional, relative to the profile repo
parallel = true

[[roles]]
name = "reviewer"                      # required
usage = "Reviews a diff. Never edits code."  # required: when the lead hands it work
model = "sonnet"                       # opus, sonnet, haiku, fable, inherit, or a claude-* model ID
tools = ["Read", "Grep"]               # optional; default is every tool
skills = ["review-checklist"]          # optional; preloaded into the role
effort = "high"                        # optional: low, medium, high, xhigh, max
prompt_file = "roles/reviewer.md"      # optional; or prompt = "..." (not both)
```

One path belongs to one profile. bondi refuses two installed profiles that claim the same file,
folder, role or lead in the same Claude folder.

## automations.toml

```toml
[[watches]]
name = "pr-merged"                     # required
check = "gh pr view {pr} --repo {repo} --json state -q .state"   # required: a shell command
done_when = "MERGED|CLOSED"            # required: a regex on the check's output
description = "Tell me when a pull request merges or closes"
args = ["repo", "pr"]                  # filled by: bondi _watches pr-merged <repo> <pr>
every = "2m"                           # 10s minimum
timeout = "24h"
notify = true
model = "haiku"                        # for then
then = "PR {pr} is {output}. Summarize what landed."   # optional follow-up prompt
tools = []                             # tools the follow-up may use
cwd = "~/code/app"

[[routines]]
name = "pr-sweep"                      # required
schedule = "7 * * * 1-5"               # required: 5-field cron, local time
prompt = "/review-queue"               # required: prompt or prompt_file, exactly one
description = "Review the open PR queue"
runner = "local"                       # local, desktop, cloud
model = "sonnet"
tools = ["Read", "Grep", "Bash(gh pr *)"]
cwd = "~/code/app"
notify = true
timeout = "30m"                        # the run is stopped after this
```

Durations are a number and a unit: `30s`, `2m`, `1h`, `1d`.

## Environment

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | the default Claude folder, instead of `~/.claude` |
| `BONDI_CLAUDE` | the `claude` executable routines and follow-ups run |
| `BONDI_NOTIFY_CMD` | a command that receives `<title> <message>` instead of the desktop notification |
