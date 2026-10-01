<h1 align="center">🚌 bondi</h1>

<p align="center">Keep your Claude Code skills, memory and settings the same on every machine.</p>

A **profile** is a git repo with your own skills, memory and config, plus an optional team of roles
and automations. Keep `personal` for every machine and `work` for work machines.

## Quick start

```bash
alias bondi='uvx --from git+https://github.com/fborello-lambda/bondi bondi'

bondi export personal --remote <git-url>   # first machine: edit the suggested bondi.toml, save, done
bondi add <git-url>                        # every other machine
bondi sync                                 # from then on, on any machine
```

Or ask Claude: the bundled `bondi-create` skill writes the profile with you. Run `bondi` alone to see
your profiles, what is waiting to sync, and anything that needs you. bondi shows its plan, backs up what
it replaces, and asks before it deletes anything; `-y` answers yes.

## What it does

- **Only your own things.** Skills from `npx skills`, login state, transcripts and tokens stay behind.
- **Any machine.** Paths are relative to the Claude folder or `~`. Project memory follows the GitHub repo,
  wherever each machine keeps its checkout, and spawned agents get it too. [Project memory](docs/sync.md#project-memory)
- **Real files.** Three-way sync, backups, and conflicts that wait for you. [How sync works](docs/sync.md)
- **A team.** One lead session hands work to roles, each a subagent with its own model. [Team](docs/team.md)
- **Automations.** Watches poll for free and notify you; routines run on a schedule. [Automations](docs/automations.md)
- **Bundled skills.** `bondi-create` builds profiles with you, `bondi-memory` keeps memory in one place, and
  `bondi-notify` sends a desktop notification. bondi keeps them current in every Claude folder it manages.

## Commands

| Command | What it does |
|---|---|
| `bondi` | show your profiles, what is waiting to sync, and anything that needs you |
| `bondi export [name]` | make a profile from this machine: bondi suggests a `bondi.toml`, you edit and save it |
| `bondi add <git-url>` | put a profile you already have on this machine |
| `bondi sync` | send this machine's changes and get the other machines' changes |
| `bondi edit [profile]` | open a profile's `bondi.toml` in your editor, check it, and sync |

To stop syncing a profile, delete its folder in `~/.config/bondi/profiles/`; the next sync forgets it.
The file format is in the [reference](docs/reference.md).

## Develop

```bash
uv run --with pytest pytest -q
sandbox/try.sh    # a playground with a copy of your setup; your real files are never written
```

MIT license.
