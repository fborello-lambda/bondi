"""The lead skill, the role subagents and the schedules skill that bondi generates from bondi.toml."""

import json
from pathlib import Path

from bondi import layout
from bondi.manifest import Lead, Manifest, Role

MODELS = ["opus", "sonnet", "haiku", "fable", "inherit"]

ROLE_TEMPLATES = [
    Role(name="planner", model="opus", tools=["Read", "Grep", "Glob", "Bash"],
         usage="Turns a goal into an ordered plan: files to touch, risks, and the checks that prove it works. "
               "Reads code, never edits it. Use before any non-trivial change."),
    Role(name="implementer", model="sonnet",
         usage="Implements one scoped task end to end with tests, then reports what changed and how it was "
               "verified. Use for coding tasks the lead hands off."),
    Role(name="reviewer", model="sonnet", tools=["Read", "Grep", "Glob", "Bash"],
         usage="Reviews a diff or branch for correctness, safety and style, and reports findings ranked by "
               "severity. Never edits code. Use after an implementer finishes."),
]


def lead_template(profile: str) -> Lead:
    return Lead(name=f"{profile}-lead", model="opus",
                usage="Start or resume orchestrated work: plan it, hand tasks to the roles, verify what they "
                      "report, and report back.")


def _read(checkout: Path, rel: str) -> str:
    p = checkout / rel
    if p.is_symlink() or not p.resolve().is_relative_to(checkout.resolve()):
        raise FileNotFoundError(f"{rel} must be a real file inside the profile repo, not a link")
    if not p.is_file():
        raise FileNotFoundError(f"{rel} is named in bondi.toml but missing from the bondi repo")
    return p.read_text()


def role_file(cdir: Path, role: Role) -> Path:
    return cdir / "agents" / f"{role.name}.md"


def lead_dir(cdir: Path, lead: Lead) -> Path:
    return cdir / "skills" / lead.name


def schedules_dir(cdir: Path, m: Manifest) -> Path:
    return cdir / "skills" / f"{m.name}-schedules"


def render_role(role: Role, checkout: Path) -> str:
    front = [f"name: {role.name}", f"description: {json.dumps(role.usage)}", f"model: {role.model}"]
    if role.tools:
        front.append(f"tools: {', '.join(role.tools)}")
    if role.skills:
        front.append(f"skills: {json.dumps(role.skills)}")
    if role.effort:
        front.append(f"effort: {role.effort}")
    body = _read(checkout, role.prompt_file) if role.prompt_file else role.prompt
    body = body.strip() or (
        f"You are the {role.name} role. {role.usage}\n\n"
        "The lead session hands you one task at a time. Do it yourself; do not hand it on.\n\n"
        "Report back with:\n- what you did,\n- what you checked and how,\n- what is left or uncertain.\n"
    )
    return "---\n" + "\n".join(front) + "\n---\n\n" + body.rstrip() + "\n"


def render_lead(m: Manifest, checkout: Path) -> dict[str, str]:
    lead = m.lead
    rows = "\n".join(f"| `{r.name}` | {r.model} | {r.usage} |" for r in m.roles) or "| (none yet) | | |"
    pace = ("Run independent tasks in parallel, one role per task." if lead.parallel
            else "Hand off one task at a time. Wait for its report before the next hand-off.")
    files = {}
    playbook = ""
    if lead.playbook:
        files["playbook.md"] = _read(checkout, lead.playbook)
        playbook = "\n## Playbook\n\nRead [playbook.md](playbook.md) before the first hand-off. It holds the house rules for this work.\n"
    files["SKILL.md"] = f"""---
name: {lead.name}
description: {json.dumps(lead.usage + " Use when the user starts or resumes work as the lead, or asks to orchestrate, plan and delegate.")}
---

# {lead.name}

You are the lead of this session. You own the plan and the context. The roles do the work. You verify
it before you report.

Run this session on **{lead.model}**. If it runs on another model, tell the user once: restart with
`claude --model {lead.model}`.

## Team

| Role | Model | Hand it |
|---|---|---|
{rows}

## Hand off work

- Use the Agent tool with `subagent_type` set to the role name.
- {pace}
- Give every brief: the goal, the context (paths, links, issue), the constraints, what "done" means,
  and what to report.
- Keep work that needs your context (decisions, trade-offs, talking with the user) in this session.

## Verify before you report

- Treat a role's report as a claim. Check it: read the diff, re-run the check it names, open the link.
- Never relay an unverified result as done. Say what you verified and how.

## Report

Lead with the outcome in one line. Then what changed, what you verified, and what is left for the user.
{playbook}"""
    return files


def render_schedules(m: Manifest, checkout: Path) -> str | None:
    items = [r for r in m.routines if r.runner in ("desktop", "cloud")]
    if not items:
        return None
    parts = [f"""---
name: {m.name}-schedules
description: {json.dumps(f"Register or update the scheduled routines of the {m.name} profile. Use after bondi add or bondi sync reports routines to register, or when the user asks to set up these schedules.")}
---

# {m.name} schedules

bondi carries these routines but cannot register desktop or cloud schedules itself. Register each one
with your own tools, then tell the user what you registered.

- **desktop**: runs in the Claude desktop app while it is open. Use the scheduled-tasks tools: update the
  task when one with this ID exists, otherwise create it, with the cron expression and the prompt below.
- **cloud**: runs in the cloud, even when this machine is off. Use the `/schedule` skill to create or
  update a routine with this name, schedule, model and prompt.
"""]
    for r in items:
        prompt = (_read(checkout, r.prompt_file) if r.prompt_file else r.prompt).strip()
        parts.append(f"""
## {r.name}

- runner: {r.runner}
- task ID: `{r.name}`
- schedule: `{r.schedule}` (local time)
- model: {r.model}{' (the desktop app picks its own model)' if r.runner == 'desktop' else ''}
{f'- about: {r.description}' if r.description else ''}

```text
{prompt}
```
""")
    return "".join(parts)


def output_paths(m: Manifest, cdir: Path) -> list[Path]:
    """Where outputs land, without reading prompt files. Used for overlap checks."""
    paths = [role_file(cdir, r) for r in m.roles]
    if m.lead:
        paths.append(lead_dir(cdir, m.lead))
    if any(r.runner in ("desktop", "cloud") for r in m.routines):
        paths.append(schedules_dir(cdir, m))
    return paths


def outputs(m: Manifest, cdir: Path, checkout: Path) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for r in m.roles:
        out[role_file(cdir, r)] = render_role(r, checkout)
    if m.lead:
        for name, text in render_lead(m, checkout).items():
            out[lead_dir(cdir, m.lead) / name] = text
    sched = render_schedules(m, checkout)
    if sched:
        out[schedules_dir(cdir, m) / "SKILL.md"] = sched
    return out


def generated_skill_names(m: Manifest) -> set[str]:
    names = {f"{m.name}-schedules"}
    if m.lead:
        names.add(m.lead.name)
    return names


def start_command(cdir: Path, m: Manifest, home_claude: Path) -> str | None:
    if not m.lead:
        return None
    prefix = "" if cdir.resolve() == home_claude.resolve() else f"CLAUDE_CONFIG_DIR={layout.collapse(cdir)} "
    return f'{prefix}claude --model {m.lead.model} "/{m.lead.name}"'
