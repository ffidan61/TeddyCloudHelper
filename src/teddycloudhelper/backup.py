"""tar.gz backup and restore of a project's configuration and certificates.

Backed up: tool state, rendered configs, TeddyCloud's ``config/`` and
``certs/`` (including the irreplaceable dumped box certs), our
``webui-pki/`` and ``security/``. **Never** audio content — ``content/``
and ``library/`` are huge and re-downloadable/user-managed.

Restore only ever extracts the known top-level items above, refuses
absolute paths, ``..`` and link members, and is preceded by an automatic
safety backup in the menu flow.
"""

from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

# Top-level items included in (and accepted back from) a backup.
BACKUP_ITEMS = (
    "teddycloudhelper.json",
    "docker-compose.yml",
    "config",
    "certs",
    "webui-pki",
    "security",
    "nginx",
)
BACKUP_DIRNAME = "backups"


class BackupError(Exception):
    """A backup or restore operation failed."""


def default_backup_dir(project_dir: Path) -> Path:
    return project_dir / BACKUP_DIRNAME


def create_backup(project_dir: Path, dest_dir: Path | None = None) -> Path:
    """Pack all existing BACKUP_ITEMS into a timestamped tar.gz."""
    items = [project_dir / name for name in BACKUP_ITEMS if (project_dir / name).exists()]
    if not items:
        raise BackupError(f"Nothing to back up in {project_dir}.")
    dest_dir = dest_dir or default_backup_dir(project_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(dest_dir)
    with tarfile.open(path, "w:gz") as tar:
        for item in items:
            tar.add(item, arcname=item.name)
    return path


def list_backups(project_dir: Path) -> list[Path]:
    """Backups in the default directory, newest first. Read live."""
    directory = default_backup_dir(project_dir)
    if not directory.is_dir():
        return []
    return sorted(
        directory.glob("teddycloudhelper-backup-*.tar.gz"),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )


def archive_members(archive: Path) -> list[str]:
    """Member names of a backup archive (validated, for display)."""
    with _open(archive) as tar:
        members = tar.getmembers()
        for member in members:
            _validate_member(member)
        return [member.name for member in members]


def restore_backup(project_dir: Path, archive: Path) -> list[str]:
    """Extract a backup over the project directory. Returns restored names."""
    with _open(archive) as tar:
        members = tar.getmembers()
        if not members:
            raise BackupError(f"{archive} is empty.")
        for member in members:
            _validate_member(member)
        try:
            tar.extractall(project_dir, members=members, filter="data")
        except TypeError:  # Python < 3.11.4 has no extraction filters
            tar.extractall(project_dir, members=members)  # noqa: S202 - members validated above
    return [member.name for member in members]


def _open(archive: Path) -> tarfile.TarFile:
    try:
        return tarfile.open(archive, "r:gz")
    except FileNotFoundError:
        raise BackupError(f"{archive} not found.") from None
    except tarfile.TarError as exc:
        raise BackupError(f"{archive} is not a readable tar.gz archive: {exc}") from exc


def _validate_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts:
        raise BackupError(f"Refusing unsafe path in archive: {member.name!r}")
    if not name.parts or name.parts[0] not in BACKUP_ITEMS:
        raise BackupError(
            f"Unexpected item {member.name!r} in archive — not a "
            "TeddyCloudHelper backup? (audio content is never restored)"
        )
    if not (member.isfile() or member.isdir()):
        raise BackupError(f"Refusing non-regular member: {member.name!r}")


def _unique_path(dest_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = dest_dir / f"teddycloudhelper-backup-{stamp}.tar.gz"
    counter = 2
    while path.exists():
        path = dest_dir / f"teddycloudhelper-backup-{stamp}-{counter}.tar.gz"
        counter += 1
    return path
