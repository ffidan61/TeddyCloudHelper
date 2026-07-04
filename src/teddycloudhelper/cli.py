"""Entry point: preflight checks, then the interactive main menu loop."""

from __future__ import annotations

import subprocess

from rich.panel import Panel

from teddycloudhelper import __version__, ui, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.menus import backup as backup_menu
from teddycloudhelper.menus import certs as certs_menu
from teddycloudhelper.menus import docker as docker_menu
from teddycloudhelper.menus import security as security_menu


def _tool_available(args: list[str]) -> bool:
    try:
        result = subprocess.run(args, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def preflight() -> list[str]:
    """Return human-readable warnings for missing prerequisites."""
    warnings = []
    if not _tool_available(["docker", "version", "--format", "json"]):
        warnings.append(
            "Docker is not available (not installed, or the daemon is not running).\n"
            "Docker-related actions will fail until this is fixed."
        )
    elif not _tool_available(["docker", "compose", "version"]):
        warnings.append(
            "The 'docker compose' plugin is missing. Install Docker Compose v2."
        )
    return warnings


MENU_ACTIONS: list[tuple[str, str]] = [
    ("Set up a new TeddyCloud project", "wizard"),
    ("Manage Docker services", "docker"),
    ("Manage certificates", "certs"),
    ("Security (Basic Auth, IP allowlist)", "security"),
    ("Backup / restore", "backup"),
    ("Quit", "quit"),
]


def _dispatch(action: str) -> bool:
    """Run one menu action. Returns False when the loop should stop."""
    if action == "quit":
        return False
    if action == "wizard":
        wizard.run()
    elif action == "docker":
        docker_menu.run()
    elif action == "certs":
        certs_menu.run()
    elif action == "security":
        security_menu.run()
    elif action == "backup":
        backup_menu.run()
    return True


def main() -> int:
    console = ui.console
    console.print(
        Panel(
            f"TeddyCloudHelper v{__version__} — set up and manage a TeddyCloud server",
            border_style="cyan",
        )
    )

    for warning in preflight():
        ui.error_panel(warning, title="Preflight warning")

    last_project = state_mod.load_last_project()
    if last_project is not None:
        console.print(f"Last used project: [bold]{last_project}[/bold]")

    while True:
        try:
            action = ui.menu("What do you want to do?", MENU_ACTIONS)
        except ui.Cancelled:
            break
        try:
            if not _dispatch(action):
                break
        except ui.Cancelled:
            continue  # user backed out of a prompt inside an action
        except Exception as exc:  # noqa: BLE001 — keep the menu alive on any failure
            ui.error_panel(f"{type(exc).__name__}: {exc}")

    console.print("Bye!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
