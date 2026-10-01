"""Helper commands for the bundled skills and the scheduler: _scan, _tidy, _watches, _schedule, _notify."""

import json
import os
from datetime import datetime
from pathlib import Path

from bondi import automations, layout, projects, scan, state, tidy, ui
from bondi.common import UserError, ago, pick_claude_dir
from bondi.manifest import ManifestError
from bondi.ui import Choice


# ---------- scan ----------

def cmd_scan(args) -> int:
    inv = scan.inventory(pick_claude_dir(args.claude_dir))
    if args.json:
        print(json.dumps(inv, indent=2))
        return 0
    ui.heading(f"Claude folder {inv['claude_dir']}")
    if inv["other_claude_dirs"]:
        ui.note(f"Other Claude folders here: {', '.join(inv['other_claude_dirs'])}")
    own = [s for s in inv["skills"] if not s["third_party"]]
    what = lambda s: (f"link -> {s['target']}" + ("" if s["target_exists"] else "  BROKEN")) if s["kind"] == "link" \
        else s["description"]
    ui.table(["Your skills", "What it is", "Profile"], [[s["name"], what(s), s["owned_by"]] for s in own])
    third = [s["name"] for s in inv["skills"] if s["third_party"]]
    if third:
        ui.note(f"Not carried (npx skills reinstalls them): {', '.join(third)}")
    rows = [[f["path"], f"{f['files']} file(s)", f["owned_by"]] for f in inv["files"] + inv["dirs"]]
    rows += [[f"project memory: {p['github'] or p['repo'] or p['folder']}", f"{p['memory_files']} file(s)", p["owned_by"]]
             for p in inv["projects"]]
    rows += [[f"~/{f['path']}", "home file", f["owned_by"]] for f in inv["home_files"]]
    ui.say()
    ui.table(["Memory and config", "Size", "Profile"], rows)
    cdir = layout.expand(inv["claude_dir"])
    for gh, roots in projects.scattered(cdir).items():
        ui.warn(f"{gh} has memory in {len(roots)} checkouts: {', '.join(layout.collapse(r) for r in roots)}. "
                "Carry it in a profile and bondi sync merges and links them.")
    for mem in projects.strays(cdir):
        ui.warn(f"{layout.collapse(mem)}: its checkout is gone, so Claude never loads it. Move what you need, then delete it.")
    return 0


# ---------- tidy ----------

def summary(f: tidy.Findings) -> str:
    parts = [f"{len(g.links)} skill link(s) into {layout.collapse(g.source)}" for g in f.groups]
    parts += [f"{len(f.broken)} broken link(s)"] if f.broken else []
    parts += [f"{len(f.leftovers)} conflict leftover(s)"] if f.leftovers else []
    parts += [f"{len(f.hardcoded)} file(s) with absolute home paths"] if f.hardcoded else []
    return "; ".join(parts)


def run_tidy(f: tidy.Findings, args) -> int:
    from bondi.common import backup_dir
    bdir, changes = backup_dir(), 0
    into = layout.expand(args.links_into).resolve() if getattr(args, "links_into", None) else None
    dry = getattr(args, "dry_run", False)

    def act(verb: str, path: Path, fn) -> None:
        nonlocal changes
        present, past = verb.split("|")
        ui.say(f"    {'would ' + present if dry else past}: {layout.collapse(path)}")
        if not dry:
            fn(path, bdir)
            changes += 1

    for g in f.groups:
        ui.say(f"\n  {len(g.links)} skill link(s) into {layout.collapse(g.source)}: {', '.join(p.name for p in g.links)}")
        if into is not None:
            picks = {p: args.do for p in g.links if into in Path(os.path.realpath(p)).parents}
            if not picks:
                continue
            if not dry and not ui.confirm(f"  {args.do} {len(picks)} link(s)?", default=True):
                ui.hint("skipped (pass -y to apply without a terminal)")
                continue
        elif ui.interactive():
            how = ui.select("  What should happen to these links?", [
                Choice("Keep them as links", None),
                Choice("Copy the skills here and drop the links", "materialize", hint="they stop depending on that repo"),
                Choice("Remove the links", "remove", hint="the skills disappear from this Claude folder"),
                Choice("Decide per skill", "each"),
            ], None)
            if how == "each":
                picks = {p: ui.select(f"  {p.name}", [Choice("Keep the link", None), Choice("Copy here", "materialize"),
                                                     Choice("Remove", "remove")], None) for p in g.links}
            else:
                picks = {p: how for p in g.links}
        else:
            continue
        for p, how in picks.items():
            if how == "materialize":
                act("copy the skill in place of the link|copied in place of the link", p, tidy.materialize)
            elif how == "remove":
                act("remove the link|removed the link", p, tidy.remove_link)

    if f.broken:
        ui.say(f"\n  Broken links: {', '.join(layout.collapse(p) for p in f.broken)}")
        if getattr(args, "remove_broken", False) or (ui.interactive() and ui.confirm("  Remove them?", default=True)):
            for p in f.broken:
                act("remove the broken link|removed the broken link", p, tidy.remove_link)
    if f.leftovers:
        ui.say(f"\n  {len(f.leftovers)} *.bondi-conflict file(s) left from earlier conflicts")
        if getattr(args, "remove_leftovers", False) or (ui.interactive() and ui.confirm("  Remove them?", default=False)):
            for p in f.leftovers:
                act("remove|removed", p, tidy.remove_file)
    if f.hardcoded:
        ui.say(f"\n  Files with absolute paths under {layout.home()} (they break on another machine):")
        for p in f.hardcoded:
            ui.say(f"    {layout.collapse(p)}")
            for line in tidy.home_lines(p)[:3]:
                ui.note(f"      {line}")
            if ui.interactive() and not dry and ui.select("    Change it?", [
                    Choice("Leave it", False), Choice(f"Replace {layout.home()}/ with ~/", True)], False):
                ui.say(f"    replaced {tidy.rewrite_home(p, bdir)} occurrence(s)")
                changes += 1
    if changes and bdir.exists():
        ui.note(f"\n  Backups: {layout.collapse(bdir)}")
    return changes


def cmd_tidy(args) -> int:
    if args.do and not args.links_into:
        raise UserError("--do needs --links-into <folder> to say which links")
    cdir = pick_claude_dir(args.claude_dir)
    f = tidy.find(cdir)
    ui.heading(f"Housekeeping in {layout.collapse(cdir)}")
    if f.empty():
        ui.done("Nothing to tidy.")
        return 0
    ui.say(f"Found: {summary(f)}")
    n = run_tidy(f, args)
    ui.say("\n--dry-run: nothing changed." if args.dry_run else f"\n{n} change(s) made.")
    return 0


# ---------- team ----------



# ---------- watch ----------

def list_watches() -> None:
    errors: list[str] = []
    templates = [(s, w) for s in automations.sources(errors) for w in s.watches]
    for e in errors:
        ui.warn(e)
    if templates:
        ui.table(["Watch", "From", "Arguments", "What it does"],
                 [[w.name, s.label, " ".join(f"<{a}>" for a in w.args), w.description or w.check] for s, w in templates])
    else:
        ui.say("No watch templates yet. Add [[watches]] to a profile's automations.toml "
               f"(bondi edit <profile> --automations) or to {layout.collapse(layout.local_automations())}.")
    running = automations.runs()
    if running:
        ui.say()
        ui.table(["Run", "Status", "Checks", "Last output", "Started"],
                 [[r.id, r.status, str(r.checks), r.last_output[:50], ago(r.started_at)] for r in running[:15]])


def cmd_watch(args) -> int:
    if args.list or not args.name:
        list_watches()
        if not args.name and not args.list:
            ui.hint("Start one: bondi _watches <name> <args...>   Stop one: bondi _watches --stop <run>")
        return 0
    if args.stop:
        run = automations.stop(args.name)
        ui.done(f"Stopped {run.id}.")
        return 0
    if args.log:
        run = automations.load_run(args.name)
        ui.say(run.log.read_text() if run.log.exists() else "(no log yet)")
        return 0
    source, w = automations.find_watch(args.name)
    run = automations.prepare(source, w, args.values)
    ui.say(f"Check: {run.check}")
    ui.say(f"Done when the output matches: {run.done_when}   every {w.every}, for at most {w.timeout}")
    if run.then:
        ui.say(f"Then: {w.model} runs a follow-up prompt" + (f" with tools {', '.join(run.tools)}" if run.tools else ""))
    if args.foreground:
        automations.poll(run)
        ui.done(f"{run.status}: {run.last_output}")
        return 0
    automations.start(run)
    ui.done(f"Watching in the background as {run.id}. You get a notification when it ends.")
    ui.hint(f"Follow it: bondi _watches --log {run.id}   Stop it: bondi _watches --stop {run.id}")
    return 0


def cmd_watch_worker(args) -> int:
    automations.poll(automations.load_run(args.run_id))
    return 0


# ---------- schedule ----------

def cmd_schedule(args) -> int:
    local = automations.local_routines()
    if args.action == "on":
        if not local:
            ui.warn("no local routines yet; the scheduler would have nothing to run")
        _, warning = automations.bondi_command()
        if warning:
            ui.warn(warning)
        how = "a launchd agent that runs `bondi _tick` every minute" if os.uname().sysname == "Darwin" \
            else "a crontab line that runs `bondi _tick` every minute"
        if not ui.confirm(f"Install {how}?", default=not warning):
            return 1
        ui.done(f"Scheduler on ({automations.scheduler_on()}).")
        return 0
    if args.action == "off":
        automations.scheduler_off()
        ui.done("Scheduler off. Local routines stop running.")
        return 0
    if args.action == "run":
        if not args.routine:
            raise UserError("name the routine to run now: bondi _schedule run <profile>/<routine>")
        hits = [(s, r) for s, r in local if args.routine in (automations.routine_key(s, r), f"{s.label}/{r.name}", r.name)]
        if not hits:
            raise UserError(f"no local routine '{args.routine}'. See: bondi _schedule")
        if len(hits) > 1:
            raise UserError(f"'{args.routine}' matches {', '.join(automations.routine_key(s, r) for s, r in hits)}; name one")
        automations.run_routine(*hits[0])
        ui.done(f"Started {args.routine}. Log: {layout.collapse(state.logs_dir())}/")
        return 0
    last = automations.state.read_json(automations.state.routines_file(), {})
    ui.heading(f"Scheduler: {automations.scheduler_status()}")
    errors: list[str] = []
    rows = [[automations.routine_key(s, r), r.runner, r.schedule, r.model,
             last.get(automations.routine_key(s, r), {}).get("minute", "never")]
            for s in automations.sources(errors) for r in s.routines]
    for e in errors:
        ui.warn(e)
    if rows:
        ui.table(["Routine", "Runner", "Schedule", "Model", "Last run"], rows)
    else:
        ui.say("No routines yet. Add [[routines]] with: bondi edit <profile> --automations")
    return 0


def cmd_routine_worker(args) -> int:
    return automations.run_routine_now(args.key)


def cmd_notify(args) -> int:
    automations.notify(args.title, args.message or "", args.sound or "")
    return 0


def cmd_tick(args) -> int:
    try:
        automations.tick(datetime.now())
    except ManifestError as e:
        print(f"bondi tick: {e}")
    return 0




