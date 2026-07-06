"""Entry point: preflight checks, then the interactive main menu loop.

``--doctor`` runs the health checks non-interactively (for cron) and exits
non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from rich.panel import Panel

from teddycloudhelper import __version__, doctor, ui, updates, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import letsencrypt
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.menus import backup as backup_menu
from teddycloudhelper.menus import boxes as boxes_menu
from teddycloudhelper.menus import certs as certs_menu
from teddycloudhelper.menus import docker as docker_menu
from teddycloudhelper.menus import doctor as doctor_menu
from teddycloudhelper.menus import settings as settings_menu


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


def _warn_expiring_cert(project: Path) -> None:
    """LE sends no expiry emails anymore — the startup check is the notice."""
    try:
        state = state_mod.load_state(project)
        warning = letsencrypt.renewal_warning(project, state)
    except (state_mod.StateError, CertError):
        return  # startup must never break over this
    if warning:
        ui.error_panel(warning, title="Certificate warning")


MENU_ACTIONS: list[tuple[str, str]] = [
    ("Set up a new TeddyCloud project", "wizard"),
    ("Project settings (change single options)", "settings"),
    ("Health check (doctor)", "doctor"),
    ("Show known boxes", "boxes"),
    ("Manage Docker services", "docker"),
    ("Manage certificates", "certs"),
    ("Backup / restore", "backup"),
    ("Quit", "quit"),
]


def _dispatch(action: str) -> bool:
    """Run one menu action. Returns False when the loop should stop."""
    if action == "quit":
        return False
    if action == "wizard":
        wizard.run()
    elif action == "settings":
        settings_menu.run()
    elif action == "doctor":
        doctor_menu.run()
    elif action == "boxes":
        boxes_menu.run()
    elif action == "docker":
        docker_menu.run()
    elif action == "certs":
        certs_menu.run()
    elif action == "backup":
        backup_menu.run()
    return True


def _headless_doctor(project: Path | None) -> int:
    """Run the checks without prompts; exit 1 on failures, 2 on setup errors."""
    project = project or state_mod.load_last_project()
    if project is None or not project.is_dir():
        ui.error_panel(
            "No project found — pass --project /path/to/your/teddycloud/project."
        )
        return 2
    try:
        state = state_mod.load_state(project)
    except state_mod.StateError as exc:
        ui.error_panel(str(exc))
        return 2
    ui.console.print(f"Checking [bold]{project}[/bold]…")
    results = doctor.run_checks(project, state)
    state_mod.save_state(state, project)  # CA fingerprint recording
    doctor_menu.show_results(results)
    return 1 if any(r.status == "fail" for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="teddycloudhelper",
        description="Interactive toolkit to set up and manage a TeddyCloud server.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="run the health checks non-interactively and exit "
        "(exit code 1 on failures — cron-friendly)",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="project directory for --doctor (default: the last used project)",
    )
    args = parser.parse_args(argv)
    if args.doctor:
        return _headless_doctor(args.project)

    console = ui.console
    console.print(
        Panel(
            f"TeddyCloudHelper v{__version__} — set up and manage a TeddyCloud server",
            border_style="cyan",
        )
    )

    notice = updates.update_notice(__version__)
    if notice:
        console.print(f"[yellow]{notice}[/yellow]")

    for warning in preflight():
        ui.error_panel(warning, title="Preflight warning")

    last_project = state_mod.load_last_project()
    if last_project is not None:
        console.print(f"Last used project: [bold]{last_project}[/bold]")
        _warn_expiring_cert(last_project)

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
