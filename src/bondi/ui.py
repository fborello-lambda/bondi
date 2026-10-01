"""Prompts and output. Arrow-key menus in a terminal; every prompt has a scripted answer for -y and for Claude."""

import sys
from dataclasses import dataclass
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.markup import escape
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)
_assume_yes = False

STYLE = questionary.Style([
    ("qmark", "fg:#5f87d7 bold"),
    ("question", "bold"),
    ("pointer", "fg:#5f87d7 bold"),
    ("highlighted", "fg:#5f87d7 bold"),
    ("selected", "fg:#5faf5f"),
    ("instruction", "fg:#808080 italic"),
])


@dataclass
class Choice:
    label: str
    value: Any
    checked: bool = False
    hint: str = ""


def set_assume_yes(value: bool) -> None:
    global _assume_yes
    _assume_yes = value


def interactive() -> bool:
    return not _assume_yes and sys.stdin.isatty() and sys.stdout.isatty()


def say(msg: str = "") -> None:
    console.print(msg, markup=False)


def note(msg: str) -> None:
    console.print(msg, style="dim", markup=False)


def done(msg: str) -> None:
    console.print(Text("✓ ", style="green") + Text(msg))


def hint(msg: str) -> None:
    console.print(Text("→ ", style="blue") + Text(msg, style="dim"))


def warn(msg: str) -> None:
    err.print(f"! {msg}", style="yellow", markup=False)


def heading(title: str, body: str = "") -> None:
    if body:
        console.print(Panel(Text(body), title=escape(title), title_align="left", border_style="blue", expand=False))
    else:
        console.print(Text("\n" + title, style="bold blue"))


def step(n: int, total: int, title: str, help_text: str = "") -> None:
    console.print(Text(f"\nStep {n} of {total}: {title}", style="bold"))
    if help_text:
        console.print(help_text, style="dim", markup=False)


def table(columns: list[str], rows: list[list], title: str = "") -> None:
    """Plain strings are shown as-is (no markup); pass rich Text for styled cells."""
    t = Table(title=title or None, show_header=True, header_style="bold", box=None, pad_edge=False)
    for c in columns:
        t.add_column(c, overflow="fold")
    for r in rows:
        t.add_row(*[c if isinstance(c, Text) else Text(str(c)) for c in r])
    console.print(t)


def _ask(q):
    ans = q.ask()
    if ans is None:
        raise KeyboardInterrupt
    return ans


def confirm(msg: str, default: bool = False) -> bool:
    """Without a terminal, only --yes counts as consent."""
    if _assume_yes:
        return True
    if not interactive():
        return False
    return _ask(questionary.confirm(msg, default=default, style=STYLE))


def text(msg: str, default: str = "", instruction: str = "", validate=None) -> str:
    if not interactive():
        return default
    return _ask(questionary.text(msg, default=default, instruction=instruction or None, validate=validate, style=STYLE)).strip()


def select(msg: str, choices: list[Choice], default: Any = None) -> Any:
    if not interactive():
        return default if any(c.value == default for c in choices) else choices[0].value
    # questionary replaces a None value with the label, so pass indexes and map back.
    qs = [questionary.Choice(c.label + (f"  ({c.hint})" if c.hint else ""), value=i) for i, c in enumerate(choices)]
    start = next((qs[i] for i, c in enumerate(choices) if c.value == default), None)
    i = _ask(questionary.select(msg, choices=qs, default=start, style=STYLE, instruction="(arrows to move, Enter to pick)"))
    return choices[i].value


def checkbox(msg: str, choices: list[Choice]) -> list[Any]:
    if not interactive() or not choices:
        return [c.value for c in choices if c.checked]
    qs = [questionary.Choice(c.label + (f"  {c.hint}" if c.hint else ""), value=i, checked=c.checked) for i, c in enumerate(choices)]
    picked = _ask(questionary.checkbox(msg, choices=qs, style=STYLE,
                                       instruction="(space to toggle, a = all, i = invert, Enter to confirm)"))
    return [choices[i].value for i in picked]
