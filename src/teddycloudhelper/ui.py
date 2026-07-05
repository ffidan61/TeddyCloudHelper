"""Console UI primitives: a shared rich Console plus questionary wrappers.

All interactive prompts go through this module so Ctrl-C / EOF behave the
same everywhere: questionary returns ``None`` when the user cancels, and the
wrappers translate that into :class:`Cancelled`, which callers (menu loops)
catch to step back instead of crashing.
"""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

console = Console()


class Cancelled(Exception):
    """The user cancelled a prompt (Ctrl-C / Esc / EOF)."""


def _ask(question: questionary.Question):
    # .ask() returns None on Ctrl-C; prompt_toolkit raises EOFError on Ctrl-D.
    try:
        answer = question.ask()
    except EOFError:
        raise Cancelled from None
    if answer is None:
        raise Cancelled
    return answer


def menu(title: str, choices: list[tuple[str, str]]) -> str:
    """Show a select menu. ``choices`` are (label, value) pairs; returns the value."""
    return _ask(
        questionary.select(
            title,
            choices=[questionary.Choice(title=label, value=value) for label, value in choices],
        )
    )


def confirm(message: str, default: bool = False) -> bool:
    return _ask(questionary.confirm(message, default=default))


def ask_text(message: str, default: str = "") -> str:
    return _ask(questionary.text(message, default=default))


def ask_password(message: str) -> str:
    return _ask(questionary.password(message))


def ask_path(message: str, default: str = "", must_exist: bool = False) -> Path:
    def validate(value: str) -> bool | str:
        if not value.strip():
            return "Please enter a path."
        if must_exist and not Path(value).expanduser().exists():
            return f"{value} does not exist."
        return True

    answer = _ask(questionary.path(message, default=default, validate=validate))
    return Path(answer).expanduser()


def error_panel(message: str, title: str = "Error") -> None:
    console.print(Panel(message, title=title, border_style="red"))


def warn_panel(message: str, title: str = "Note") -> None:
    """Orange panel for expected-but-important notices (not errors)."""
    console.print(Panel(message, title=title, border_style="orange1"))


def info_panel(message: str, title: str = "Info") -> None:
    console.print(Panel(message, title=title, border_style="cyan"))
