"""bondi export and bondi edit: write a profile's bondi.toml in your own editor."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bondi import layout, scan, ui
from bondi.cmd_profiles import add_new, cmd_sync
from bondi.common import UserError, installs, pick_claude_dir
from bondi.manifest import AUTOMATIONS, MANIFEST, NAME_RE, ManifestError, load
from bondi.ui import Choice

DOCS = "https://github.com/fborello-lambda/bondi/blob/main/docs/reference.md"


def editor() -> str:
    chosen = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if chosen:
        return chosen
    found = next((e for e in ("vim", "vi", "nano") if shutil.which(e)), None)
    if not found:
        raise UserError("no text editor found. Set $EDITOR, for example: export EDITOR=nano")
    return found


def terminal() -> bool:
    """A real terminal, even under -y: the editor needs one, the questions do not."""
    return ui.interactive() or (sys.stdin.isatty() and sys.stdout.isatty())


def open_editor(path: Path) -> bool:
    """Run the editor the way git does, so $EDITOR may hold arguments or quotes. False when it fails."""
    try:
        return subprocess.run(["sh", "-c", f'{editor()} "$@"', "editor", str(path)]).returncode == 0
    except OSError as e:
        raise UserError(f"could not start the editor: {e}") from e


def edit_until_valid(path: Path, check: Path, name: str | None = None) -> bool:
    """Open the editor until the profile file parses. False means the user gave up."""
    while True:
        if not open_editor(path):
            ui.warn("the editor exited with an error, so bondi keeps the file as it is and stops here")
            return False
        try:
            m = load(check)
            if name and m.name != name:
                raise ManifestError(f"name must stay '{name}'. To use another name, export a new profile")
            return True
        except ManifestError as e:
            ui.warn(str(e))
            if not ui.confirm("Open it again to fix this?", default=True):
                return False


def _toml_list(items: list[str], indent: str = "  ") -> str:
    if not items:
        return "[]"
    return "[\n" + "".join(f"{indent}{json.dumps(i)},\n" for i in items) + "]"


def draft(cdir: Path, name: str) -> str:
    inv = scan.inventory(cdir)
    # Files another profile generates here (its lead skill, its role agents) are not this machine's to export.
    here = [i for i in installs() if i.cdir.resolve() == cdir.resolve()]
    generated = {layout.expand(k) for i in here for k in i.generated}
    roles = any(i.repo.exists() and _has_roles(i) for i in here)
    skills = [s["name"] for s in inv["skills"] if not s["third_party"] and not s["owned_by"]
              and not any(g.is_relative_to(cdir / "skills" / s["name"]) for g in generated)]
    files = [f["path"] for f in inv["files"] if not f["owned_by"]]
    dirs = [d["path"] for d in inv["dirs"] if not d["owned_by"] and not (d["path"] == "agents" and roles)]
    homes = [f["path"] for f in inv["home_files"] if not f["owned_by"]]
    repos = sorted({p["github"] for p in inv["projects"] if p["github"] and not p["owned_by"]}, key=str.lower)
    no_origin = [p["repo"] or p["folder"] for p in inv["projects"] if not p["github"] and not p["owned_by"]]
    out = [
        f"# Profile '{name}': what this machine shares with your other machines.",
        "# Delete a line to leave something out. Save and close the editor to create the profile.",
        f"# Every option: {DOCS}",
        "",
        f"name = {json.dumps(name)}",
        'description = ""',
        "",
        f"[claude]  # inside {inv['claude_dir']}",
        "# Your own skills. Skills that npx skills installed stay out; it reinstalls them itself.",
        f"skills = {_toml_list(skills)}",
        f"files = {_toml_list(files)}",
        f"dirs = {_toml_list(dirs)}",
        "",
        "[home]  # inside your home folder",
        f"# Found here: {', '.join(homes)}. Add the ones you want, for example \".zshrc\"." if homes else "# For example \".zshrc\".",
        "files = []",
    ]
    for gh in repos:
        out += ["", "[[projects]]  # this repo's memory, found on every machine by its GitHub origin", f"github = {json.dumps(gh)}"]
    if no_origin:
        out += ["", f"# Project memory with no GitHub origin, so it cannot travel: {', '.join(no_origin)}"]
    return "\n".join(out) + "\n"


def _has_roles(inst) -> bool:
    try:
        return bool(load(inst.repo / MANIFEST).roles)
    except ManifestError:
        return False


def cmd_export(args) -> int:
    if args.source:
        return add_new(args, layout.expand(args.source))
    if not terminal():
        raise UserError("bondi export opens your editor, so it needs a terminal. In a script, pass --from <bondi.toml>.")
    cdir = pick_claude_dir(args.claude_dir)
    name = args.name or ui.text("Profile name", "personal", instruction="(for example personal or work)")
    if not NAME_RE.match(name):
        raise UserError(f"'{name}' cannot be a profile name: use lowercase letters, digits and dashes")
    if layout.checkout_dir(name).exists():
        raise UserError(f"a profile named '{name}' is already here. Change it with: bondi edit {name}")
    path = layout.data_dir() / "drafts" / name / MANIFEST
    if path.is_file():
        ui.note(f"Opening the draft you started earlier: {layout.collapse(path)}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft(cdir, name))
    if not edit_until_valid(path, path):
        ui.say(f"Not created. Your draft stays at {layout.collapse(path)}; bondi export {name} opens it again.")
        return 1
    rc = add_new(args, path)
    if rc == 0 and not args.dry_run:
        shutil.rmtree(path.parent, ignore_errors=True)
    return rc


def cmd_edit(args) -> int:
    found = [i for i in installs() if i.repo.exists()]
    if args.name:
        found = [i for i in found if i.name == args.name]
    if not found:
        raise UserError(f"no profile named '{args.name}' here" if args.name else "there is no profile here yet. Make one with: bondi export")
    inst = found[0] if len(found) == 1 else ui.select("Which profile?", [Choice(i.label, i) for i in found], found[0])
    target = inst.repo / (AUTOMATIONS if args.automations else MANIFEST)
    if not terminal():
        ui.say(str(target))
        return 0
    if args.automations and not target.exists():
        target.write_text(f"# Checks and scheduled tasks for profile '{inst.name}'. Every option: {DOCS}\n")
    if not edit_until_valid(target, inst.repo / MANIFEST, name=inst.name):
        raise UserError(f"{target.name} still has an error, so bondi sync skips '{inst.name}' until you fix it")
    ui.done(f"{target.name} is valid.")
    if ui.confirm("Sync now, so the change reaches this machine and the profile?", default=True):
        args.names, args.dry_run = [inst.name], False
        return cmd_sync(args)
    ui.hint("The change goes out with the next bondi sync.")
    return 0
