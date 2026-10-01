---
name: bondi-create
description: Turn what one machine has in its Claude folder (your own skills, memory, CLAUDE.md, settings, project memory, shell files) into machine-agnostic bondi profiles such as personal and work, optionally with a lead and roles and with watches and routines, write each bondi.toml and automations.toml, and create the profile repos with the bondi CLI. Use when the user asks to create a bondi profile, export or share their Claude setup, split memory and skills between profiles, set up a team of roles, or add watches and routines.
---

# bondi-create

A profile is a git repo with a `bondi.toml` (what it carries, and the team) and an optional
`automations.toml` (watches and routines). This skill reads the machine, agrees the split with the
user, writes the files, and creates the repos. The bondi CLI does the file work.

## 1. Find the CLI

Run `bondi --version`. If it is missing, use
`uvx --from git+https://github.com/fborello-lambda/bondi bondi` in its place. Call the result `BONDI`.

## 2. Pick the Claude folder

Run `BONDI _scan --json`. If it fails with "more than one Claude folder", ask the user which folder the
profile is for, and pass `--claude-dir <dir>` to every later command. Never pick one yourself: the
folders can belong to different Claude accounts.

## 3. Housekeeping

Run `BONDI _tidy --dry-run`. Report what it found: skill links into other repos, broken links, files
with absolute home paths. Offer the fixes, and run them only after a yes.

## 4. Agree the split

Use only scan items with `third_party: false` and an empty `owned_by`. Show them grouped, then propose
one owner per item:

- global instructions, settings, general memory, hooks and home files go to `personal`;
- project memory goes to the profile for that kind of work;
- a skill goes to the profile whose work it serves (read its description).

Then ask two questions:

- **Team?** A lead session plus roles (each with a model and a job). If yes, agree the lead's model,
  whether it runs tasks in parallel, each role's model, tools and preloaded skills, and whether a
  role needs a longer prompt in `roles/<name>.md`.
- **Automations?** Watches to start on demand (for example "tell me when a PR merges") and routines on
  a schedule. For each routine agree the schedule, the runner (`local`, `desktop` or `cloud`), the
  model, and the exact `tools` it may use.

Do not continue until the user agrees.

## 5. Write the files

Write them in a temporary folder, with prompt files beside them, and show each file in full.

`bondi.toml`:

```toml
name = "work"
description = "Only on work machines"

[claude]
skills = ["deploy-check"]
dirs = ["memory"]

[[projects]]
github = "acme/app"               # the repo's GitHub origin; never a local path

[lead]
name = "work-lead"
model = "opus"
usage = "Start or resume orchestrated work: plan, hand off, verify, report."

[[roles]]
name = "reviewer"
model = "sonnet"
usage = "Reviews a diff for correctness and safety. Never edits code."
tools = ["Read", "Grep", "Glob", "Bash"]
```

`automations.toml`:

```toml
[[watches]]
name = "pr-merged"
args = ["repo", "pr"]
check = "gh pr view {pr} --repo {repo} --json state -q .state"
done_when = "MERGED|CLOSED"

[[routines]]
name = "pr-sweep"
schedule = "7 * * * 1-5"
runner = "local"
model = "sonnet"
prompt = "/review-queue"
tools = ["Read", "Grep", "Bash(gh pr *)"]
```

The full format is in the bondi repo's `docs/reference.md`.

## 6. Create the profile

1. Run `BONDI export --from <dir>/bondi.toml [--claude-dir <dir>] --dry-run -v` and show the plan.
2. After a yes, run it again with `-y` in place of `--dry-run`.
3. Report every file bondi left out as a possible credential. Do not force it in.
4. If there are local routines, tell the user that `BONDI sync`, run once in their terminal, asks to turn
   the scheduler on. If there are desktop or cloud routines, run `/<profile>-schedules` in this session
   to register them, after a yes.

## 7. Publish

Creating a GitHub repo is an external action. Show the exact command and wait for a yes:

```bash
gh repo create <owner>/bondi-<name> --private
```

Then run `git -C ~/.config/bondi/profiles/<name> remote add origin git@github.com:<owner>/bondi-<name>.git`
and `BONDI sync <name> -y`, which pushes it. On other machines the user runs `BONDI add <url>`, then
`BONDI sync` from then on.

## Never

- Never add `.claude.json`, `.credentials.json`, `settings.local.json`, transcripts, sessions or keys.
- Never create a public repo for a profile that holds memory.
- Never push, create repos, register schedules or edit live files without the user's yes.
