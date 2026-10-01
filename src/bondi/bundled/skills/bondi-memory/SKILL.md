---
name: bondi-memory
description: Where to save a memory when bondi manages this machine, so memory never scatters. Use before you write, move or merge a memory file, when the user asks where their memory lives, or when a spawned agent needs the project's rules.
---

# bondi-memory

bondi keeps one memory per scope and syncs it to the user's other machines with `bondi sync`.

## Where each memory goes

- **Rules for every project** go in `<Claude folder>/memory/`. `CLAUDE.md` imports them, so every session and every spawned agent loads them.
- **Facts about one repo** go in that repo's project memory, the folder Claude Code uses by default. It is shared by every worktree of the repo, and by every other checkout of it on this machine (bondi links those to one folder).
- Never write memory anywhere else: not into a worktree, not into a second copy of a rule that already exists in global memory.

Before you save, check whether an existing file already covers the fact. Update that file instead of adding a new one.

## Spawned agents

Subagents load `CLAUDE.md` but not project memory. bondi adds a `SubagentStart` hook to `settings.json` that runs `scripts/agent_memory.sh` in this folder. It gives each agent the repo's `MEMORY.md` index and the folder path. `agent_memory = false` in `bondi.toml` turns it off. If an agent needs a rule, put the rule in memory, not only in the agent's brief.

## Commands

- `bondi`: shows where each repo's memory lives, memory whose checkout is gone, and repos with memory in more than one place.
- `bondi sync --ask`: change which checkout holds a repo's memory on this machine.
- `bondi sync`: send memory edits to the profile and get the other machines' edits.
