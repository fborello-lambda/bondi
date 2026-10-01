"""bondi command line: add, sync, export and edit. Running bondi alone shows where things stand."""

import sys
from types import SimpleNamespace
from typing import Annotated, Optional

import typer

from bondi import __version__, ui
from bondi.common import UserError
from bondi.gitops import GitError
from bondi.manifest import ManifestError

app = typer.Typer(
    help="Keep your Claude Code skills, memory and settings the same on every machine.",
    epilog="Run bondi with no command to see your profiles, what is waiting to sync, and anything that needs you. "
           "bondi backs up every file it replaces and asks before it deletes; -y answers yes. Claude can do all "
           "of this for you with the bondi-create skill.",
    add_completion=False, no_args_is_help=False, context_settings={"help_option_names": ["-h", "--help"]},
)

def flag(*names: str, help: str = "") -> typer.models.OptionInfo:
    """An on/off option with only its own name: no --no-x twin and no default line in --help."""
    return typer.Option(*names, help=help, show_default=False)


Yes = Annotated[bool, flag("-y", "--yes", help="Answer yes to every question.")]
Verbose = Annotated[bool, flag("-v", "--verbose", help="List every file.")]
ClaudeDir = Annotated[Optional[str], typer.Option("--claude-dir", metavar="DIR",
                                                  help="Claude folder to use (default ~/.claude, or $CLAUDE_CONFIG_DIR).")]
DryRun = Annotated[bool, flag("--dry-run", help="Show what would change; write nothing.")]


def _run(fn, internal: bool = False, **fields) -> None:
    """Call a command with an argparse-style namespace, and turn its result or error into an exit code."""
    args = SimpleNamespace(**{"yes": False, "verbose": False, "claude_dir": None, "dry_run": False, **fields})
    ui.set_assume_yes(args.yes)
    from bondi import projects
    projects.clear_cache()
    try:
        if not internal:
            refresh_bundle()
        code = fn(args)
    except (UserError, ManifestError, GitError) as e:
        ui.err.print(f"bondi: {e}", style="red", markup=False)
        code = 1
    except (KeyboardInterrupt, EOFError):
        ui.err.print("\nbondi: cancelled. Nothing more was written.", markup=False)
        code = 130
    raise typer.Exit(code or 0)


def refresh_bundle() -> None:
    from bondi import bundle
    try:
        bundle.ensure_all()
    except Exception as e:  # never let housekeeping block the command the user asked for
        ui.warn(f"could not refresh bondi's bundled skills: {e}")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"bondi {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def overview(ctx: typer.Context,
             version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, show_default=False,
                                                   help="Show the version.")] = False) -> None:
    if ctx.invoked_subcommand is None:
        from bondi import overview as screen
        _run(lambda args: screen.show())


@app.command()
def add(git_url: Annotated[str, typer.Argument(metavar="GIT-URL", help="The profile's git URL.")],
        dry_run: DryRun = False, yes: Yes = False, verbose: Verbose = False, claude_dir: ClaudeDir = None) -> None:
    """Put a profile you already have on this machine."""
    from bondi.cmd_profiles import cmd_add
    _run(cmd_add, source=git_url, dry_run=dry_run, yes=yes, verbose=verbose, claude_dir=claude_dir)


@app.command()
def sync(profiles: Annotated[Optional[list[str]], typer.Argument(metavar="[PROFILE]...",
                                                                  help="Only these profiles (default: all).")] = None,
         dry_run: DryRun = False,
         keep_local: Annotated[bool, flag("--keep-local", help="Settle every conflict with this machine's version.")] = False,
         keep_remote: Annotated[bool, flag("--keep-remote", help="Settle every conflict with the profile's version.")] = False,
         allow_mass_delete: Annotated[bool, flag("--allow-mass-delete", help="Allow deleting most of a profile's files at once.")] = False,
         ask: Annotated[bool, flag("--ask", help="Ask again which checkout holds each repo's memory.")] = False,
         yes: Yes = False, verbose: Verbose = False, claude_dir: ClaudeDir = None) -> None:
    """Send this machine's changes and get your other machines' changes."""
    from bondi.cmd_profiles import cmd_sync
    if keep_local and keep_remote:
        raise typer.BadParameter("pick one of --keep-local and --keep-remote")
    _run(cmd_sync, names=profiles or [], dry_run=dry_run, keep_local=keep_local, keep_remote=keep_remote,
         allow_mass_delete=allow_mass_delete, ask=ask, yes=yes, verbose=verbose, claude_dir=claude_dir)


@app.command()
def export(name: Annotated[Optional[str], typer.Argument(help="Profile name, for example personal or work.")] = None,
           from_file: Annotated[Optional[str], typer.Option("--from", metavar="FILE",
                                                            help="Use this bondi.toml instead of opening the editor.")] = None,
           remote: Annotated[Optional[str], typer.Option(metavar="URL",
                                                         help="A private git repo to push the new profile to.")] = None,
           dry_run: DryRun = False, yes: Yes = False, verbose: Verbose = False, claude_dir: ClaudeDir = None) -> None:
    """Make a profile from this machine: bondi writes a bondi.toml and opens your editor."""
    from bondi.cmd_export import cmd_export
    _run(cmd_export, name=name, source=from_file, remote=remote, dry_run=dry_run, yes=yes, verbose=verbose,
         claude_dir=claude_dir)


@app.command()
def edit(profile: Annotated[Optional[str], typer.Argument(help="The profile to edit.")] = None,
         automations: Annotated[bool, flag("--automations", help="Open automations.toml instead.")] = False,
         yes: Yes = False, verbose: Verbose = False, claude_dir: ClaudeDir = None) -> None:
    """Open a profile's bondi.toml in your editor ($VISUAL, $EDITOR, or vim), check it, and sync."""
    from bondi.cmd_export import cmd_edit
    _run(cmd_edit, name=profile, automations=automations, yes=yes, verbose=verbose, claude_dir=claude_dir)


# Helpers for the bundled skills, scripts, hooks and the scheduler. They stay out of --help.

@app.command("_scan", hidden=True)
def scan_(json_out: Annotated[bool, flag("--json")] = False, claude_dir: ClaudeDir = None) -> None:
    from bondi.cmd_tools import cmd_scan
    # --json feeds the bondi-create skill, so nothing else may print to stdout.
    _run(cmd_scan, internal=json_out, json=json_out, claude_dir=claude_dir)


@app.command("_tidy", hidden=True)
def tidy_(links_into: Annotated[Optional[str], typer.Option(metavar="DIR")] = None,
          do: Annotated[Optional[str], typer.Option(help="materialize or remove")] = None,
          remove_broken: Annotated[bool, flag("--remove-broken")] = False,
          remove_leftovers: Annotated[bool, flag("--remove-leftovers")] = False,
          dry_run: DryRun = False, yes: Yes = False, claude_dir: ClaudeDir = None) -> None:
    from bondi.cmd_tools import cmd_tidy
    if do not in (None, "materialize", "remove"):
        raise typer.BadParameter("--do takes materialize or remove")
    if bool(do) != bool(links_into):
        raise typer.BadParameter("--links-into and --do go together")
    _run(cmd_tidy, links_into=links_into, do=do, remove_broken=remove_broken, remove_leftovers=remove_leftovers,
         dry_run=dry_run, yes=yes, claude_dir=claude_dir)


@app.command("_watches", hidden=True)
def watches_(name: Annotated[Optional[str], typer.Argument()] = None,
             values: Annotated[Optional[list[str]], typer.Argument()] = None,
             list_runs: Annotated[bool, flag("--list")] = False, stop: Annotated[bool, flag("--stop")] = False,
             log: Annotated[bool, flag("--log")] = False, foreground: Annotated[bool, flag("--foreground")] = False, yes: Yes = False, claude_dir: ClaudeDir = None) -> None:
    from bondi.cmd_tools import cmd_watch
    _run(cmd_watch, name=name, values=values or [], list=list_runs, stop=stop, log=log, foreground=foreground,
         yes=yes, claude_dir=claude_dir)


@app.command("_schedule", hidden=True)
def schedule_(action: Annotated[str, typer.Argument(help="status, on, off or run")] = "status",
              routine: Annotated[Optional[str], typer.Argument()] = None, yes: Yes = False) -> None:
    from bondi.cmd_tools import cmd_schedule
    if action not in ("status", "on", "off", "run"):
        raise typer.BadParameter("the action is status, on, off or run")
    _run(cmd_schedule, action=action, routine=routine, yes=yes)


@app.command("_notify", hidden=True)
def notify_(title: str, message: Annotated[Optional[str], typer.Argument()] = None,
            sound: Annotated[Optional[str], typer.Option(metavar="NAME")] = None) -> None:
    from bondi.cmd_tools import cmd_notify
    _run(cmd_notify, title=title, message=message, sound=sound)


@app.command("_watch", hidden=True)
def watch_worker_(run_id: str) -> None:
    from bondi.cmd_tools import cmd_watch_worker
    _run(cmd_watch_worker, internal=True, run_id=run_id)


@app.command("_tick", hidden=True)
def tick_() -> None:
    from bondi.cmd_tools import cmd_tick
    _run(cmd_tick, internal=True)


@app.command("_routine", hidden=True)
def routine_worker_(key: str) -> None:
    from bondi.cmd_tools import cmd_routine_worker
    _run(cmd_routine_worker, internal=True, key=key)


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=sys.argv[1:] if argv is None else argv, prog_name="bondi")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
