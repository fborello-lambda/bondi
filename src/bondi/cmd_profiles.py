"""The two commands that change things: bondi add and bondi sync."""

import difflib
import shutil
import socket
import uuid
from pathlib import Path

from rich.text import Text

from bondi import gitops, guard, layout, projects, state, ui
from bondi.common import (UserError, apply_team, ago, automation_hints, backup_dir, finish, installs, manifest_of,
                          overlaps, pick_claude_dir, selected, show_plan, upsert)
from bondi.gitops import GitError
from bondi.manifest import AUTOMATIONS, MANIFEST, Manifest, ManifestError, load
from bondi.syncer import BLOCKED, CONFLICT, DEL_LIVE, DEL_REPO, Action, apply, plan
from bondi.ui import Choice


def readme(m: Manifest) -> str:
    lines = [f"# {m.name}", "", m.description, "", "A [bondi](https://github.com/fborello-lambda/bondi) profile.",
             "", "```bash", "bondi add <this-repo-url>", "```", "", "## Carries", ""]
    lines += [f"- skill `{s}`" for s in m.skills] + [f"- `{f}`" for f in m.files] + [f"- `{d}/`" for d in m.dirs]
    lines += [f"- project memory for `{p.github}`" for p in m.projects] + [f"- `~/{f}`" for f in m.home_files]
    if m.lead:
        lines += ["", "## Team", "", f"- lead `{m.lead.name}` ({m.lead.model}): {m.lead.usage}"]
        lines += [f"- role `{r.name}` ({r.model}): {r.usage}" for r in m.roles]
    if m.watches or m.routines:
        lines += ["", "## Automations", "", "See `automations.toml`."]
    return "\n".join(lines) + "\n"


def cmd_add(args) -> int:
    return add_existing(args, args.source)


def add_new(args, src: Path) -> int:
    cdir = pick_claude_dir(args.claude_dir)
    src = src / MANIFEST if src.is_dir() else src
    if not src.is_file():
        raise UserError(f"{layout.collapse(src)} does not exist. A new profile starts from a folder that holds a {MANIFEST}.")
    m = load(src)
    forget_deleted(dry_run=args.dry_run)
    checkout = layout.checkout_dir(m.name)
    if checkout.exists():
        raise UserError(f"a profile named '{m.name}' is already on this machine, at {layout.collapse(checkout)}. "
                        f"To change it, edit its {MANIFEST} there and run bondi sync.")
    bdir = backup_dir()
    homes, _ = place_projects(m, cdir, {}, checkout, bdir, dry_run=args.dry_run)
    errs = guard.manifest_errors(m) + overlaps(m, cdir, checkout, homes=homes)
    if errs:
        raise UserError("this profile cannot be created:\n  " + "\n  ".join(errs))

    gitops.init(checkout)
    try:
        (checkout / MANIFEST).write_text(src.read_text())
        if src.with_name(AUTOMATIONS).is_file():
            (checkout / AUTOMATIONS).write_text(src.with_name(AUTOMATIONS).read_text())
        for rel in [r.prompt_file for r in m.roles if r.prompt_file] + [r.prompt_file for r in m.routines if r.prompt_file] \
                + ([m.lead.playbook] if m.lead and m.lead.playbook else []):
            if not (src.parent / rel).is_file():
                raise UserError(f"{MANIFEST} names {rel}, but there is no such file next to it")
            (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src.parent / rel, checkout / rel)
        actions = plan(m, cdir, checkout, None, [], homes=homes)
        ui.heading(f"New profile '{m.name}'", f"bondi copies these files from {layout.collapse(cdir)} into the profile. "
                   "Nothing on this machine changes.")
        show_plan(actions, args.verbose)
        for a in actions:
            if a.op == BLOCKED:
                ui.warn(f"{layout.collapse(a.item.live)} stays out of the profile: {a.note}")
        if args.dry_run:
            ui.say("Dry run: nothing written.")
            shutil.rmtree(checkout)
            return 0
        if not ui.confirm(f"Create profile '{m.name}'?", default=True):
            shutil.rmtree(checkout)
            ui.say("Cancelled. Nothing written." + ("" if ui.interactive() else " Rerun with -y to answer yes."))
            return 1
        # Merge other checkouts of each repo only now, after the yes, then plan again to save what they held.
        link_projects(cdir, m, homes, bdir)
        actions = plan(m, cdir, checkout, None, [], homes=homes)
    except BaseException:
        shutil.rmtree(checkout, ignore_errors=True)
        raise
    res = apply(actions, checkout, bdir)
    (checkout / "README.md").write_text(readme(m))
    (checkout / ".gitignore").write_text(".DS_Store\n*.bondi-conflict\n*.bondi-tmp\n")
    inst = state.Install(name=m.name, claude_dir=layout.collapse(cdir), checkout=layout.collapse(checkout), projects=homes)
    apply_team(inst, m, bdir)
    if args.remote:
        gitops.set_remote(checkout, args.remote)
    finish(inst, f"bondi: create {m.name} from {socket.gethostname()}")
    upsert(inst)
    from bondi import bundle
    bundle.ensure_all([cdir])
    ui.done(f"Created profile '{m.name}' with {sum(res.applied.values())} file(s)"
            + (f" ({len(res.blocked)} left out)" if res.blocked else "") + f". It lives at {layout.collapse(checkout)}.")
    if args.remote and gitops.pushed(checkout):
        ui.say(f"It is pushed to {args.remote}. On another machine, run: bondi add {args.remote}")
    elif args.remote:
        ui.hint(f"Not pushed yet. Create the repo at {args.remote} if it does not exist, then run bondi sync.")
    else:
        ui.hint(f"To use it on other machines, give it a private git repo: "
                f"git -C {layout.collapse(checkout)} remote add origin <url>, then run bondi sync.")
    automation_hints(inst, m)
    return 0


def runs_code_notes(m: Manifest) -> list[str]:
    notes = []
    if "settings.json" in m.files:
        notes.append("settings.json: its hooks, status line and permissions apply to every Claude session")
    if "hooks" in m.dirs:
        notes.append("hooks/: scripts that settings.json can run")
    notes += [f"routine {r.name}: runs `{r.prompt[:40] or r.prompt_file}` on {r.schedule} with tools {r.tools or 'none'}"
              for r in m.routines if r.runner == "local"]
    notes += [f"watch {w.name}: runs `{w.check[:60]}`" for w in m.watches]
    return notes


def place_projects(m: Manifest, cdir: Path, homes: dict[str, str], checkout: Path, bdir: Path,
                   ask: bool = False, dry_run: bool = False, fill: bool = True) -> tuple[dict[str, str], list[str]]:
    """Pick the checkout that holds each project's memory here, and fill a new one from the profile.

    Returns the homes and the profile files that a fresh home already holds a different version of."""
    out, conflicts = {}, []
    for p in m.projects:
        if not p.memory:
            continue
        cur = homes.get(p.key)
        root = projects.choose_home(cdir, p, cur, ask=ask, dry_run=dry_run)
        if not root:
            continue
        new = layout.collapse(root)
        if new != cur and not dry_run and fill:
            old = projects.main_root(layout.expand(cur)) if cur else None
            if old:
                # The old home holds this machine's synced edits: merge it in first, and let it win.
                for note in projects.link_others(cdir, root, p.github, bdir, prefer=old):
                    ui.note(note)
            rels = projects.fill(cdir, root, checkout / "projects" / p.key / "memory", fresh=old is None)
            conflicts += [f"projects/{p.key}/memory/{r}" for r in rels]
            if cur:
                ui.note(f"{p.github}: memory now lives with {new}")
        out[p.key] = new
    return out, conflicts


def link_projects(cdir: Path, m: Manifest, homes: dict[str, str], bdir: Path) -> None:
    """Merge every other folder of each repo into its home, then link them. Runs before planning, so merged files sync."""
    for p in m.projects:
        if p.key in homes:
            for note in projects.link_others(cdir, layout.expand(homes[p.key]), p.github, bdir):
                ui.note(note)


def add_existing(args, url: str) -> int:
    forget_deleted(dry_run=args.dry_run)
    incoming = layout.data_dir() / "incoming" / uuid.uuid4().hex[:8]
    ui.note(f"Fetching {url} ...")
    gitops.clone(url, incoming)
    try:
        m = load(incoming / MANIFEST)
        errs = guard.manifest_errors(m)
        if errs:
            raise UserError("this profile asks for paths bondi never carries:\n  " + "\n  ".join(errs))
        cdir = pick_claude_dir(args.claude_dir)
        if state.find(installs(), m.name, cdir):
            raise UserError(f"profile '{m.name}' is already on this machine, in {layout.collapse(cdir)}. Run: bondi sync")
        checkout = layout.checkout_dir(m.name)
        if checkout.exists() and (gitops.remote_url(checkout) or "") != url:
            raise UserError(f"another profile named '{m.name}' lives at {layout.collapse(checkout)} with a different remote")
        # Until the yes, plan from the fresh clone, so a dry run or a cancel leaves no profile folder behind.
        source = checkout if checkout.exists() else incoming
        if checkout.exists() and not args.dry_run:
            gitops.pull(checkout)
        bdir = backup_dir()
        homes, _ = place_projects(m, cdir, {}, source, bdir, dry_run=args.dry_run, fill=False)
        errs = overlaps(m, cdir, source, skip_name=m.name, homes=homes)
        if errs:
            raise UserError("this profile overlaps with one already installed:\n  " + "\n  ".join(errs))
        actions = plan(m, cdir, source, None, [], homes=homes)
        ui.heading(f"Add profile '{m.name}' to {layout.collapse(cdir)}", m.description or "")
        show_plan(actions, args.verbose)
        if m.lead:
            ui.say(f"Team: a main session, {m.lead.name} on {m.lead.model}, with "
                   + (", ".join(f"{r.name} on {r.model}" for r in m.roles) or "no helpers") + ".")
        if m.watches or m.routines:
            ui.say(f"Automations: {len(m.watches)} check(s) you can start, {len(m.routines)} scheduled task(s).")
        runs_code = runs_code_notes(m)
        if runs_code:
            ui.warn("this profile can run code on this machine. Add it only if you trust where it comes from:")
            for line in runs_code:
                ui.say(f"    {line}")
        if args.dry_run:
            ui.say("Dry run: nothing written.")
            return 0
        if (actions or m.lead) and not ui.confirm("Apply? bondi backs up every file it replaces.", default=not runs_code):
            ui.say("Cancelled. Nothing written." + ("" if ui.interactive() else " Rerun with --yes."))
            return 1
        if source == incoming:
            checkout.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(incoming), checkout)
    finally:
        shutil.rmtree(incoming, ignore_errors=True)

    link_projects(cdir, m, homes, bdir)
    actions = plan(m, cdir, checkout, None, [], homes=homes)
    res = apply(actions, checkout, bdir)
    inst = state.Install(name=m.name, claude_dir=layout.collapse(cdir), checkout=layout.collapse(checkout),
                         conflicts=res.conflicts, projects=homes)
    apply_team(inst, m, bdir)
    finish(inst, f"bondi: install {m.name} on {socket.gethostname()}")
    upsert(inst)
    from bondi import bundle
    bundle.ensure_all([cdir])
    ui.done(f"Added '{m.name}'. From now on, bondi sync keeps it in step with your other machines."
            + (f" Backups: {layout.collapse(bdir)}" if bdir.exists() else ""))
    automation_hints(inst, m)
    return 0


# ---------- sync / status ----------

def show_diff(a: Action) -> None:
    local = (a.live or b"").decode(errors="replace").splitlines()
    remote = (a.repo or b"").decode(errors="replace").splitlines()
    for line in list(difflib.unified_diff(remote, local, "profile version", "this machine", lineterm="", n=2))[:40]:
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else None
        ui.console.print(Text("    " + line, style=style))


def resolve_conflicts(actions: list[Action], keep: str | None) -> dict[str, str]:
    choices: dict[str, str] = {}
    for a in (x for x in actions if x.op == CONFLICT):
        if keep:
            choices[a.item.rel] = keep
            continue
        if not ui.interactive():
            continue
        ui.say(f"\n{layout.collapse(a.item.live)} changed on this machine and in the profile.")
        show_diff(a)
        pick = ui.select("Which version should win?", [
            Choice("Keep this machine's version", "local"),
            Choice("Take the profile's version", "remote"),
            Choice("Decide later", None, hint="the profile version waits beside the file as *.bondi-conflict"),
        ], None)
        if pick:
            choices[a.item.rel] = pick
    return choices


MASS_DELETE = 5


def sync_one(inst: state.Install, args, fetch: bool) -> bool:
    ui.heading(f"{inst.name}  ({inst.claude_dir}, last sync {ago(inst.synced_at)})")
    if not inst.cdir.is_dir():
        ui.warn(f"skipped: the Claude folder {inst.claude_dir} is missing. Restore it, or delete the profile folder {inst.checkout} to stop syncing it")
        return False
    if fetch and not args.dry_run:
        gitops.commit_all(inst.repo, f"bondi: edit {inst.name} on {socket.gethostname()}")
        try:
            ancestor, kept = gitops.pull(inst.repo)
        except GitError as e:
            ancestor, kept = None, None
            ui.warn(f"could not pull ({e}). Using the local copy.")
        if ancestor:
            inst.base = ancestor
            ui.warn(f"this machine and the remote both changed the profile while apart. Your unpushed commits are "
                    f"kept on branch {kept}; changes on both sides are compared file by file below.")
    try:
        m = manifest_of(inst)
    except ManifestError as e:
        ui.warn(f"skipped: {e}. Fix it with: bondi edit {inst.name}")
        return False
    if m.name != inst.name:
        ui.warn(f"skipped: its {MANIFEST} now says name = '{m.name}'. Renaming is not supported; set it back to "
                f"'{inst.name}', or export a new profile under the new name")
        return False
    if args.dry_run:
        homes = {p.key: inst.projects[p.key] for p in m.projects if p.key in inst.projects}
        for p in m.projects:
            if p.memory and p.key not in homes:
                ui.hint(f"{p.github} has no checkout picked here yet; bondi sync places it")
    else:
        bdir = backup_dir()
        homes, fresh_conflicts = place_projects(m, inst.cdir, inst.projects, inst.repo, bdir, ask=getattr(args, "ask", False))
        link_projects(inst.cdir, m, homes, bdir)
        inst.conflicts = sorted(set(inst.conflicts) | set(fresh_conflicts))
    errs = guard.manifest_errors(m) + overlaps(m, inst.cdir, inst.repo, skip_name=inst.name, homes=homes)
    if errs:
        ui.warn("skipped: " + "; ".join(errs))
        return False
    known_base = gitops.has_commit(inst.repo, inst.base)
    actions = plan(m, inst.cdir, inst.repo, inst.base if known_base else None, inst.conflicts,
                   first_install=inst.base is None, deferred=inst.deferred, homes=homes)
    show_plan(actions, args.verbose)
    if args.dry_run:
        apply_team(inst, m, backup_dir(), dry_run=True)
        return True
    keep = "local" if getattr(args, "keep_local", False) else "remote" if getattr(args, "keep_remote", False) else None
    choices = resolve_conflicts(actions, keep)
    planned = actions
    actions = confirm_deletes(inst, actions, getattr(args, "allow_mass_delete", False))
    kept = {id(a) for a in actions}
    inst.deferred = {a.item.rel: a.op for a in planned if a.op in (DEL_LIVE, DEL_REPO) and id(a) not in kept}
    bdir = backup_dir()
    res = apply(actions, inst.repo, bdir, choices)
    for a in actions:
        if a.item.rel in choices:
            a.item.live.with_name(a.item.live.name + ".bondi-conflict").unlink(missing_ok=True)
    inst.conflicts = res.conflicts
    inst.projects = homes
    apply_team(inst, m, bdir)
    finish(inst, f"bondi: sync {inst.name} from {socket.gethostname()}")
    upsert(inst)
    if res.conflicts:
        ui.warn(f"{len(res.conflicts)} conflict(s) left. Run bondi sync in a terminal to choose, or pass "
                "--keep-local or --keep-remote. Where both sides are files, the profile version waits beside yours as *.bondi-conflict.")
    for rel in res.blocked:
        ui.warn(f"not saved, looks like a credential: {rel}")
    automation_hints(inst, m)
    return True


def confirm_deletes(inst: state.Install, actions: list[Action], allow_mass: bool) -> list[Action]:
    """Deletes spread to every machine, so each batch needs a yes, and a mass delete needs a flag."""
    here = [a for a in actions if a.op == DEL_LIVE]
    there = [a for a in actions if a.op == DEL_REPO]
    tracked = sum(1 for p in inst.repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    if len(there) > MASS_DELETE and len(there) * 2 >= tracked and not allow_mass:
        ui.warn(f"{len(there)} of {tracked} files would be deleted from the profile. That looks like a missing "
                f"or emptied folder, so bondi deletes nothing. If it is intended, rerun with --allow-mass-delete.")
        return [a for a in actions if a.op not in (DEL_REPO, DEL_LIVE)]
    keep = actions
    if here and not ui.confirm(f"Delete {len(here)} file(s) here that were deleted in the profile? (backed up first)", default=True):
        keep = [a for a in keep if a.op != DEL_LIVE]
        ui.hint("deletions here wait for the next sync" + ("" if ui.interactive() else "; pass -y to apply them"))
    if there and not ui.confirm(f"Delete {len(there)} file(s) from the profile that you deleted here? Other machines delete them too.", default=True):
        keep = [a for a in keep if a.op != DEL_REPO]
        ui.hint("deletions in the profile wait for the next sync" + ("" if ui.interactive() else "; pass -y to apply them"))
    return keep


def forget_deleted(dry_run: bool = False) -> None:
    """Deleting a profile's folder is how you stop syncing it. Its generated files go too; your files stay."""
    from bondi import generated as gen
    found = installs()
    # The profiles folder itself must be there: a missing one is an unplugged disk, not a deletion.
    gone = [i for i in found if not i.repo.exists() and i.repo.parent.is_dir()]
    for inst in gone:
        if dry_run:
            ui.note(f"The folder of profile '{inst.name}' is gone; bondi sync would stop syncing it.")
            continue
        owned = dict(inst.generated)
        notes = gen.apply(inst, {}, backup_dir()) if owned else []
        inst.generated = owned
        gen.remember_orphans(inst)
        removed = sum(1 for n in notes if n.startswith("removed"))
        ui.note(f"The folder of profile '{inst.name}' is gone, so bondi stopped syncing it"
                + (f" and removed the {removed} file(s) it generated for it." if removed else "."))
    if gone and not dry_run:
        state.save([i for i in found if i not in gone])


def cmd_sync(args) -> int:
    forget_deleted(dry_run=args.dry_run)
    picked = selected(args.names, args.claude_dir)
    ok = all([sync_one(i, args, fetch=True) for i in picked])
    pending = [i.name for i in installs() if i.conflicts and i.name in {p.name for p in picked}]
    return 0 if ok and not pending else 1


