"""Shared project selection for submenus.

Adopting means: point the tool at a directory that already contains a compose
file, create a fresh ``teddycloudhelper.json`` there if none exists, and set
the global last-project pointer. No compose file is generated here — that is
the setup wizard's job (v0.4).
"""

from __future__ import annotations

from pathlib import Path

from teddycloudhelper import docker_cli, ui
from teddycloudhelper import state as state_mod


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


def active_project() -> Path | None:
    """Reuse the last project if it still has a compose file, else adopt one."""
    last = state_mod.load_last_project()
    if last is not None and docker_cli.find_compose_file(last) is not None:
        return last
    ui.info_panel(
        "No usable project yet — pick a directory with an existing "
        "TeddyCloud compose file."
    )
    return adopt_project()
