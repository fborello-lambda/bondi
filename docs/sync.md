# How sync works

## The three versions

For every file a profile carries, bondi compares three versions:

- **this machine**: the live file, for example `~/.claude/memory/rules.md`;
- **the profile**: the copy in the local profile repo, after pulling;
- **the last sync**: the copy at the commit this machine last synced.

| This machine | Profile | Result |
|---|---|---|
| changed | same as last sync | the machine's version is saved to the profile |
| same as last sync | changed | the profile's version is written here |
| changed | changed, differently | **conflict**: see below |
| deleted | same as last sync | the file is deleted in the profile (you are asked first) |
| same as last sync | deleted | the file is deleted here (you are asked first) |
| new file in a carried folder | | added to the profile |

Then bondi commits and pushes. A new memory file that Claude writes on one machine reaches the others
on their next `bondi sync`.

Deletes spread to every machine, so they always need a yes: in a terminal bondi asks, in a script it
waits for `-y`. A delete that waits stays pending until you agree. When most of a profile would be
deleted at once (a moved or emptied folder, say), bondi deletes nothing unless you pass
`--allow-mass-delete`. When a profile's Claude folder is missing, sync skips that profile.

## Offline on both sides

When two machines change the profile while one is offline, git cannot simply replay one on the other.
bondi keeps this machine's unpushed commits on a `bondi-local-<id>` branch in the profile repo, takes
the remote, and compares every file against the last version both sides shared. Files changed on only
one side merge on their own; files changed on both become conflicts.

## Conflicts

When a file changed on this machine and in the profile, bondi keeps your file and writes the
profile's version beside it as `<file>.bondi-conflict`. In a terminal, it shows the diff and asks
which version wins. In a script, the conflict waits until you choose, or until you pass
`--keep-local` or `--keep-remote`. A conflict never resolves itself.

## Project memory

Claude Code keeps one memory folder per repo, named after the checkout's path. A profile names the repo
instead (`github = "acme/app"`), so the same profile works wherever each machine keeps its checkout.

- bondi finds the checkout from the Claude project folders it already has, by their `origin` remote.
  With several checkouts, it asks once which one holds the memory. With none, it asks for a path or
  clones the repo to `~/code/<name>`. `bondi sync --ask` changes the choice.
- Every other checkout of the same repo gets a link to that one folder, after bondi merges its files in.
  A file that differs waits beside the kept one as `*.bondi-conflict`. Worktrees already share the main
  checkout's folder.
- Subagents load `CLAUDE.md` but not project memory. bondi adds a `SubagentStart` hook to `settings.json`
  that hands each spawned agent the repo's `MEMORY.md` index. The hook finds its script through
  `$CLAUDE_CONFIG_DIR`, so a carried `settings.json` works in any Claude folder. To turn it off, set
  `agent_memory = false` in `bondi.toml`; every machine removes it on its next sync.
- `bondi` alone reports memory whose checkout is gone, and repos with memory in more than one place.

## Backups

Every file bondi replaces or deletes on this machine is copied first to
`~/.local/share/bondi/backups/<time>/`, under its path relative to your home folder.

## What is never carried

- Login and account state: `.claude.json`, `.credentials.json`, `settings.local.json`.
- Transcripts and sessions: `*.jsonl`, `sessions/`, `session-env/`, `file-history/`.
- Caches and plugin downloads: `cache/`, `plugins/`, `telemetry/`. Plugins come back on their own from
  `enabledPlugins` in `settings.json`.
- Skills that `npx skills` installed (it keeps a lock file and reinstalls them).
- Keys and credential files, even inside a carried folder: `id_*` (not `.pub`), `*.pem`, `*.key`,
  `.env*`, `.netrc`, `.git-credentials`. Shell history, `~/.ssh` (except `~/.ssh/config`), `~/.aws`,
  `~/.docker`, `~/.kube`, and `~/.config/bondi` itself.
- Any file whose content looks like a token or a password in a URL. bondi leaves it out and says so,
  also when you resolve a conflict with `--keep-local`.
- The session keys Claude Code stamps on a memory file's frontmatter: `originSessionId`, `node_type`
  and `modified`. bondi drops them from the profile copy, so a new stamp alone is not an edit.

## Where things live on a machine

| Path | What |
|---|---|
| `~/.config/bondi/profiles/<profile>/` | the local copy of each profile repo |
| `~/.config/bondi/automations.toml` | machine-only watches and routines (optional) |
| `~/.local/share/bondi/backups/<time>/` | files bondi replaced or deleted |
| `~/.local/state/bondi/installs.json` | installed profiles, their Claude folders, and the files bondi generated |
| `~/.local/state/bondi/watches/` | watch runs and their logs |
| `~/.local/state/bondi/logs/` | routine logs |
| `<Claude folder>/skills/bondi-*` | bundled skills, refreshed on every bondi run |

bondi follows the XDG base directories. If `XDG_CONFIG_HOME`, `XDG_DATA_HOME` or `XDG_STATE_HOME`
is set to an absolute path, bondi uses it in place of `~/.config`, `~/.local/share` or `~/.local/state`.

## Symlinked skills

A skill that is a symlink, for example into a repo you cloned, travels as a small `.link` file that
holds a `~`-relative target. bondi recreates the link on install. `bondi _tidy` can replace links with
real copies when you want a skill to stop depending on another repo.

## More than one Claude folder

Most machines have one, `~/.claude`, and bondi uses it without asking (or `CLAUDE_CONFIG_DIR` when
set). When a machine has more, for example one per Claude account, bondi asks which one to use and
never guesses in a script: pass `--claude-dir <dir>`. Each install remembers its folder, and sync never
moves files between folders.

## Requirements

macOS or Linux, Python 3.11+ (uvx provides it), `git`, and the `claude` CLI for routines and watch
follow-ups. Private profile repos work with whatever auth `git` already uses: an SSH key or a
credential helper.
