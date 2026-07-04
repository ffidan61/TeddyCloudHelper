"""Docker services submenu for the active project's compose stack."""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from teddycloudhelper import docker_cli, ui
from teddycloudhelper.menus import project as project_menu

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
        return project_menu.adopt_project()
    return None


def run() -> None:
    """Submenu loop. Mirrors the main loop: errors render red and never crash."""
    project = project_menu.active_project()
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
