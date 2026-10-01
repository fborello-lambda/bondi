# Automations: watches and routines

Automations live in `automations.toml`, next to `bondi.toml` in a profile repo, so they travel with
the profile. For machine-only ones, use `~/.config/bondi/automations.toml`.

The rule behind both: **the check is code, the judgment is a model.** Polling runs a shell command and
costs nothing. A model runs only when something needs judgment.

## Watches

A watch is a template for "tell me when X happens". You start it when you need it.

```toml
[[watches]]
name = "pr-merged"
description = "Tell me when a pull request merges or closes"
args = ["repo", "pr"]
check = "gh pr view {pr} --repo {repo} --json state -q .state"
done_when = "MERGED|CLOSED"      # a regex on the check's output; name every end state
every = "2m"                     # at least 10s; 30s or more for remote APIs
timeout = "24h"
notify = true                    # a desktop notification when it ends

# Optional follow-up, run once when done_when matches:
model = "haiku"
then = "PR {pr} in {repo} is {output}. Pull main in ~/code/app and report in 3 lines."
tools = ["Bash(git pull *)", "Read"]
cwd = "~/code/app"
```

```bash
bondi _watches pr-merged acme/app 123     # starts in the background and returns
bondi _watches --list                     # templates and runs
bondi _watches --log <run>                # print a run's log
bondi _watches --stop <run>
```

- Arguments fill the `{name}` placeholders in one pass. In `check` they are shell-quoted, so an argument
  can never run as a command.
- `{output}` in `then` is the check's last output.
- The watch runs as a background process: closing the terminal does not stop it. A reboot does.
- The `bondi-create` skill offers a starter template, `pr-merged`.

## Routines

A routine runs a prompt on a schedule.

```toml
[[routines]]
name = "pr-sweep"
description = "Review the open PR queue"
schedule = "7 * * * 1-5"         # cron, local time: minute hour day-of-month month day-of-week
runner = "local"                 # local, desktop or cloud
model = "sonnet"
prompt = "/review-queue"         # or prompt_file = "routines/pr-sweep.md"
tools = ["Read", "Grep", "Bash(gh pr *)"]
cwd = "~/code/app"
notify = true                    # a notification with the first line of the result
timeout = "30m"
```

| Runner | Runs | Needs |
|---|---|---|
| `local` | on this machine through `claude -p`, while it is awake | `bondi sync` asks once per machine |
| `desktop` | in the Claude desktop app, while it is open | registering once from a Claude session (below) |
| `cloud` | in the cloud, even when this machine is off | registering once from a Claude session (below) |

**local.** The first `bondi sync` in a terminal asks to install one scheduler entry (a launchd agent on macOS, a crontab line
on Linux) that runs `bondi _tick` every minute. The tick starts each routine whose schedule matches,
at most once per minute, with the Claude folder of its profile. Logs go to
`~/.local/state/bondi/logs/`. `bondi _schedule` shows each routine and its last run;
`bondi _schedule run <profile>/<routine>` runs one now; `bondi _schedule off` removes the entry. For
routines, install bondi permanently (`uv tool install git+https://github.com/fborello-lambda/bondi`),
so the scheduler has a stable path to run.

**desktop and cloud.** Only a Claude session can register these schedules. bondi generates a
`<profile>-schedules` skill with every desktop and cloud routine; run `/<profile>-schedules` in Claude
once, and again after you change them.

## Notifications

Watches and routines notify you when they end (`notify = false` turns it off). Anything else can too:

```bash
bondi _notify "Deploy done" "acme/app is live" --sound Glass
```

Claude gets the same through the bundled `bondi-notify` skill: ask it to "notify me when the tests
pass" and it uses the skill's script. Set `BONDI_NOTIFY_CMD` to send notifications somewhere else.

## Permissions

Nobody is there to approve a tool call in a headless run. A routine or a follow-up can use the tools in
its `tools` list, plus whatever your `settings.json` already allows. Keep both short. A routine that sends messages (Slack, GitHub comments)
sends them without asking, so say in its prompt exactly what it may send.
