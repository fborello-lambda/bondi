"""What `bondi` alone shows: the profiles here, what is waiting, where memory lives, and anything that needs you."""

from bondi import __version__, automations, gitops, layout, projects, team, tidy, ui
from bondi.common import ago, installs, manifest_of
from bondi.manifest import ManifestError
from bondi.syncer import CONFLICT, plan


def _waiting(inst, m) -> str:
    base = inst.base if gitops.has_commit(inst.repo, inst.base) else None
    actions = plan(m, inst.cdir, inst.repo, base, inst.conflicts, first_install=inst.base is None,
                   deferred=inst.deferred, homes=inst.projects)
    conflicts = sum(1 for a in actions if a.op == CONFLICT)
    if not actions:
        return "in step"
    return f"{len(actions)} change(s)" + (f", {conflicts} need you" if conflicts else "")


def show() -> int:
    found = installs()
    ui.heading(f"bondi {__version__}", "Your Claude Code skills, memory and settings, the same on every machine.")
    if not found:
        ui.say("There is no profile on this machine yet.")
        ui.hint("Ask Claude to create one: it has the bondi-create skill.")
        ui.hint("Or add one you already have: bondi add <git-url>")
        return 0

    rows, notes, problems = [], [], []
    for inst in found:
        if not inst.cdir.is_dir():
            rows.append([inst.name, inst.claude_dir, "", ago(inst.synced_at), "Claude folder missing; sync skips it"])
            continue
        if not inst.repo.exists():
            rows.append([inst.name, inst.claude_dir, "", "", "folder deleted; bondi sync forgets it"])
            continue
        try:
            m = manifest_of(inst)
        except ManifestError as e:
            rows.append([inst.name, inst.claude_dir, "", ago(inst.synced_at), "bondi.toml has an error"])
            problems.append(f"{inst.name}: {e}")
            continue
        rows.append([inst.name, inst.claude_dir, gitops.remote_url(inst.repo) or "only on this machine",
                     ago(inst.synced_at), _waiting(inst, m)])
        for p in m.projects:
            home = inst.projects.get(p.key)
            notes.append(f"{p.github}: memory lives with {home}" if home
                         else f"{p.github}: no checkout here yet; bondi sync asks where it is")
        if m.lead:
            helpers = ", ".join(f"{r.name} on {r.model}" for r in m.roles) or "no helpers"
            notes.append(f"Team in '{inst.name}': main session {m.lead.name} on {m.lead.model}, with {helpers}. "
                         f"Start it: {team.start_command(inst.cdir, m, layout.home() / '.claude')}")
    ui.table(["Profile", "Claude folder", "Shared at", "Last sync", "Waiting"], rows)
    for line in notes:
        ui.say(f"  {line}")

    running = [r for r in automations.runs() if r.status == "running"]
    if running:
        ui.say(f"  Checks running now: {', '.join(r.id for r in running)}")
    errors: list[str] = []
    local = automations.local_routines(errors)
    problems += errors
    if local:
        ui.say(f"  Scheduled tasks on this machine: {len(local)}, scheduler {automations.scheduler_status()}")

    for cdir in {i.cdir.resolve(): i.cdir for i in found if i.cdir.is_dir()}.values():
        f = tidy.find(cdir)
        problems += [f"broken skill link: {layout.collapse(p)}" for p in f.broken]
        problems += [f"conflict copy left over: {layout.collapse(p)}" for p in f.leftovers]
        problems += [f"absolute home path inside {layout.collapse(p)}; other machines cannot use it" for p in f.hardcoded]
        for gh, roots in projects.scattered(cdir).items():
            problems.append(f"{gh} has memory in {len(roots)} places: {', '.join(layout.collapse(r) for r in roots)}")
        problems += [f"{layout.collapse(m)}: its checkout is gone, so Claude never loads it" for m in projects.strays(cdir)]
    if problems:
        ui.say()
        ui.warn("Needs a look:")
        for line in problems:
            ui.say(f"    {line}")
    ui.say()
    ui.hint("Run bondi sync to send this machine's changes and get the other machines'.")
    return 0
