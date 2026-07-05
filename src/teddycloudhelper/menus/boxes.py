"""Known-boxes overview: what TeddyCloud knows about connected boxes.

Queried inside the container (``compose exec``) so neither nginx routing
nor WebUI auth get in the way — same path the doctor uses.
"""

from __future__ import annotations

import json

from rich.table import Table

from teddycloudhelper import docker_cli, ui
from teddycloudhelper.menus import project as project_menu


def run() -> None:
    project = project_menu.active_project()
    if project is None:
        return
    compose = docker_cli.Compose(project)
    try:
        raw = compose.exec_service(
            "teddycloud", "curl", "-s", "http://localhost:80/api/getBoxes"
        ).stdout
    except docker_cli.DockerError as exc:
        ui.error_panel(str(exc), title="Known boxes")
        return
    try:
        boxes = json.loads(raw)["boxes"]
    except (ValueError, KeyError, TypeError):
        ui.error_panel(
            f"Unexpected answer from /api/getBoxes: {raw[:120]!r}",
            title="Known boxes",
        )
        return
    if not boxes:
        ui.info_panel(
            "TeddyCloud knows no boxes yet — no box has ever connected (or "
            "been configured). The doctor checks the whole box path."
        )
        return
    table = Table(title=f"Boxes known to TeddyCloud ({len(boxes)})")
    table.add_column("Name")
    table.add_column("MAC")
    table.add_column("Model")
    table.add_column("ID")
    for box in boxes:
        table.add_row(
            box.get("boxName") or "—",
            box.get("commonName") or "—",
            box.get("boxModel") or "—",
            box.get("ID") or "—",
        )
    ui.console.print(table)
