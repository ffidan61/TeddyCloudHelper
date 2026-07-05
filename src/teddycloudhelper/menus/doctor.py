"""Health-check ("doctor") menu action: run all checks, render one table."""

from __future__ import annotations

from rich.table import Table

from teddycloudhelper import doctor, ui
from teddycloudhelper import state as state_mod
from teddycloudhelper.menus import project as project_menu

_STYLES = {"ok": "green", "warn": "yellow", "fail": "red"}


def run() -> None:
    project = project_menu.active_project()
    if project is None:
        return
    try:
        state = state_mod.load_state(project)
    except state_mod.StateError as exc:
        ui.error_panel(str(exc), title="Health check")
        return
    ui.console.print(f"Checking [bold]{project}[/bold]…")
    results = doctor.run_checks(project, state)

    table = Table(title="Health check")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for result in results:
        style = _STYLES.get(result.status, "yellow")
        table.add_row(result.name, f"[{style}]{result.status}[/{style}]", result.detail)
    ui.console.print(table)

    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")
    if failed:
        ui.error_panel(
            f"{failed} check(s) failed, {warned} warning(s) — see the table above.",
            title="Problems found",
        )
    elif warned:
        ui.info_panel(f"No failures, {warned} warning(s) — see the table above.")
    else:
        ui.info_panel("Everything looks healthy.")
