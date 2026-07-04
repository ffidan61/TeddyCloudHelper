"""Docker services submenu: adopt an existing install, then manage its stack.

Adopting means: point the tool at a directory that already contains a compose
file, create a fresh ``teddycloudhelper.json`` there if none exists, and set
the global last-project pointer. No compose file is generated here — that is
the setup wizard's job (v0.4).
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from teddycloudhelper import docker_cli, ui
from teddycloudhelper import state as state_mod

MENU_ACTIONS: list[tuple[str, str]] = [
    ("Show service status", "status"),
    ("Start services (up -d)", "start"),
    ("Stop services", "stop"),
    ("Restart services", "restart"),
    ("Show recent logs", "logs"),
    ("Pull latest images", "pull"),
    ("Switch / adopt another project", "switch"),
    ("Back to main menu", "back"),
]


def adopt_project() -> Path | None:
    """Register a directory with an existing compose file as the active project."""
    directory = ui.ask_path(
        "TeddyCloud project directory (must contain a compose file):",
        must_exist=True,
    ).resolve()
    if docker_cli.find_compose_file(directory) is None:
        ui.error_panel(
            f"No compose file ({', '.join(docker_cli.COMPOSE_FILENAMES)}) "
            f"found in {directory}."
        )
        return None
    if not state_mod.has_state(directory):
        if not ui.confirm(
            f"No {state_mod.STATE_FILENAME} here yet. "
            "Register this directory as a TeddyCloudHelper project?",
            default=True,
        ):
            return None
        state_mod.save_state(state_mod.AppState(), directory)
    state_mod.save_last_project(directory)
    return directory


def _active_project() -> Path | None:
    """Reuse the last project if it still has a compose file, else adopt one."""
    last = state_mod.load_last_project()
    if last is not None and docker_cli.find_compose_file(last) is not None:
        return last
    ui.info_panel(
        "No usable project yet — pick a directory with an existing "
        "TeddyCloud compose file."
    )
    return adopt_project()


def _print_status(compose: docker_cli.Compose) -> None:
    services = compose.ps()
    if not services:
        ui.info_panel("No containers found for this project (not created yet?).")
        return
    table = Table(title=f"Services in {compose.project_dir}")
    table.add_column("Service")
    table.add_column("Container")
    table.add_column("State")
    table.add_column("Status")
    for svc in services:
        style = "green" if svc.state == "running" else "red"
        table.add_row(svc.service, svc.name, f"[{style}]{svc.state}[/{style}]", svc.status)
    ui.console.print(table)


def _dispatch(action: str, compose: docker_cli.Compose) -> Path | None:
    """Run one submenu action; returns a new project dir on switch."""
    if action == "status":
        _print_status(compose)
    elif action == "start":
        compose.up()
        _print_status(compose)
    elif action == "stop":
        compose.stop()
        _print_status(compose)
    elif action == "restart":
        compose.restart()
        _print_status(compose)
    elif action == "logs":
        ui.console.print(compose.logs(tail=100) or "[dim](no log output)[/dim]")
    elif action == "pull":
        compose.pull()
        ui.info_panel("Images pulled.")
        if ui.confirm("Restart services now to use the new images?", default=True):
            compose.restart()
            _print_status(compose)
    elif action == "switch":
        return adopt_project()
    return None


def run() -> None:
    """Submenu loop. Mirrors the main loop: errors render red and never crash."""
    project = _active_project()
    if project is None:
        return
    while True:
        compose = docker_cli.Compose(project)
        ui.console.print(f"Active project: [bold]{project}[/bold]")
        try:
            action = ui.menu("Docker services", MENU_ACTIONS)
        except ui.Cancelled:
            return
        if action == "back":
            return
        try:
            project = _dispatch(action, compose) or project
        except ui.Cancelled:
            continue
        except docker_cli.DockerError as exc:
            ui.error_panel(str(exc), title="Docker error")
