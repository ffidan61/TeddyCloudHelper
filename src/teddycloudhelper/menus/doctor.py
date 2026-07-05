"""Health-check ("doctor") menu action: run all checks, render one table.

``show_results`` is shared with the headless ``--doctor`` CLI mode; the
interactive extras (accepting a changed CA, creating a missing backup)
only run here.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from teddycloudhelper import backup, doctor, ui
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import server_certs
from teddycloudhelper.menus import project as project_menu
from teddycloudhelper.state import AppState

_STYLES = {"ok": "green", "warn": "yellow", "fail": "red"}


def show_results(results: list[doctor.CheckResult]) -> None:
    """Render the check table plus a one-line verdict."""
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
    # check_ca_identity records the fingerprint on first sight.
    state_mod.save_state(state, project)

    show_results(results)
    _offer_ca_acceptance(project, state)
    _offer_backup(project, results)


def _offer_ca_acceptance(project: Path, state: AppState) -> None:
    """After a CA-change failure: let the user accept the new CA as known
    (only sensible when the change was intentional and boxes are re-flashed)."""
    current = server_certs.box_ca_fingerprint(project)
    if (
        current is None
        or not state.known_ca_fingerprint
        or state.known_ca_fingerprint == current
    ):
        return
    if ui.confirm(
        "Accept the CURRENT box CA as the known one? Only do this if the "
        "change was intentional — boxes flashed against the old CA stay "
        "broken until re-flashed.",
        default=False,
    ):
        state.known_ca_fingerprint = current
        state_mod.save_state(state, project)
        ui.info_panel("New box CA recorded.")


def _offer_backup(project: Path, results: list[doctor.CheckResult]) -> None:
    """The backup check warned — fix it on the spot."""
    if not any(r.name == "Backup" and r.status == "warn" for r in results):
        return
    if not ui.confirm("Create a backup now?", default=True):
        return
    try:
        path = backup.create_backup(project)
    except backup.BackupError as exc:
        ui.error_panel(str(exc), title="Backup failed")
        return
    ui.info_panel(f"Backup written: {path}")
