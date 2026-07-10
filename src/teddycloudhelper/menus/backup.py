"""Backup submenu: create, list and restore config+certs backups.

Audio content (``content/``, ``library/``) is never included — see
:mod:`teddycloudhelper.backup`. A restore is always preceded by an
automatic safety backup of the current configuration.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.table import Table

from teddycloudhelper import backup, docker_cli, ui, wizard
from teddycloudhelper.backup import BackupError
from teddycloudhelper.menus import project as project_menu

MENU_ACTIONS: list[tuple[str, str]] = [
    ("Create a backup now", "create"),
    ("List backups", "list"),
    ("Restore a backup", "restore"),
    ("Back to main menu", "back"),
]


def _create(project: Path) -> None:
    path = backup.create_backup(project)
    ui.info_panel(
        f"Backup written: {path}\n"
        "Contains config + certificates — audio content is never included.",
        title="Backup created",
    )


def _list(project: Path) -> None:
    backups = backup.list_backups(project)
    if not backups:
        ui.info_panel(f"No backups in {backup.default_backup_dir(project)} yet.")
        return
    table = Table(title=f"Backups in {backup.default_backup_dir(project)}")
    table.add_column("File")
    table.add_column("Size", justify="right")
    table.add_column("Created")
    for path in backups:
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(path.name, f"{stat.st_size / 1024:.0f} KiB", created)
    ui.console.print(table)


def _restore(project: Path) -> None:
    backups = backup.list_backups(project)
    if backups:
        choices = [(p.name, str(p)) for p in backups] + [
            ("Pick another archive file…", "other")
        ]
        selected = ui.menu("Restore which backup?", choices)
        archive = (
            ui.ask_path("Path to the backup archive:", must_exist=True)
            if selected == "other"
            else Path(selected)
        )
    else:
        archive = ui.ask_path("Path to the backup archive:", must_exist=True)

    members = backup.archive_members(archive)
    top_level = sorted({name.split("/", 1)[0] for name in members})
    ui.info_panel(
        f"{archive.name} contains {len(members)} item(s):\n" + ", ".join(top_level)
    )
    if not ui.confirm(
        "Restore this backup? Current config and certificates are overwritten "
        "(a safety backup is created first).",
        default=False,
    ):
        return
    safety = backup.create_backup(project)
    restored = backup.restore_backup(project, archive)
    ui.info_panel(
        f"Restored {len(restored)} item(s) from {archive.name}.\n"
        f"Safety backup of the previous config: {safety}",
        title="Backup restored",
    )
    if ui.confirm("Restart services now to run with the restored config?", default=True):
        # Restored configs are bind-mounted — a plain `up` would leave running
        # containers on the old files; this also `nginx -t`s the restored conf.
        wizard.restart_services(project)


def run() -> None:
    project = project_menu.active_project()
    if project is None:
        return
    while True:
        ui.console.print(f"Active project: [bold]{project}[/bold]")
        try:
            action = ui.menu("Backup / restore", MENU_ACTIONS)
        except ui.Cancelled:
            return
        if action == "back":
            return
        try:
            if action == "create":
                _create(project)
            elif action == "list":
                _list(project)
            elif action == "restore":
                _restore(project)
        except ui.Cancelled:
            continue
        except (BackupError, docker_cli.DockerError) as exc:
            ui.error_panel(str(exc), title="Backup error")
