"""Persistent tool state.

Two layers of persistence:

* Per-project state in ``<project>/teddycloudhelper.json`` (:class:`AppState`).
  Lives inside the TeddyCloud project directory so backups capture it
  automatically. Only configuration chosen by the user is stored here —
  derived state (container status, certificate lists) is always read live.
* A tiny global pointer file in the platform user-config dir that remembers
  the last used project directory, so the tool can offer to reopen it.

The on-disk schema is versioned (``schema_version``). Loading an older file
runs the registered migrations in order; a timestamped ``.bak`` copy is
written before any migration touches the file.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from platformdirs import user_config_dir

SCHEMA_VERSION = 2
STATE_FILENAME = "teddycloudhelper.json"


def _migrate_1_to_2(data: dict) -> dict:
    """v2 drops letsencrypt_email (LE sends no mails; certbot registers
    without one) in favour of a plain letsencrypt_enabled flag."""
    data["letsencrypt_enabled"] = bool(data.pop("letsencrypt_email", ""))
    return data


# Migration i transforms a raw dict from schema_version i to i+1.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _migrate_1_to_2}


class StateError(Exception):
    """Raised when a state file cannot be read or migrated."""


@dataclass
class AppState:
    """User-chosen configuration for one TeddyCloud project."""

    schema_version: int = SCHEMA_VERSION
    # "direct" (TeddyCloud exposes its ports itself) or "nginx" (reverse proxy).
    deployment_mode: str = "direct"
    # Image channel: "latest" (stable) or "develop" (bleeding edge).
    teddycloud_image_tag: str = "latest"
    # Run the TeddyCloud process as this host UID/GID instead of root
    # (upstream PUID/PGID support since v0.7.0; 0 = keep running as root).
    puid: int = 0
    pgid: int = 0
    # "shared" (WebUI and box share 443 via SNI split) or "separate" (own port).
    webui_port_mode: str = "separate"
    webui_hostname: str = ""
    webui_port: int = 8443
    http_port: int = 80
    # Require browser client certs (own CA, mTLS at nginx) for the WebUI.
    webui_client_cert_auth: bool = False
    # "selfsigned" (webui-pki/server/) or "letsencrypt" (certbot-managed).
    webui_tls_mode: str = "selfsigned"
    # Enables the certbot side-container in the compose file.
    letsencrypt_enabled: bool = False
    basic_auth_enabled: bool = False
    ip_allowlist: list[str] = field(default_factory=list)
    # When both the allowlist and Basic Auth are on: False = nginx's default
    # (both must pass); True = `satisfy any` (allowed IPs skip the password).
    ip_bypasses_basic_auth: bool = False
    # Monotone serial counter for issued client certificates.
    next_serial: int = 1
    # SHA-256 of certs/server/ca.der when last seen — flashed boxes trust
    # exactly this CA, so a change must never go unnoticed (doctor check).
    known_ca_fingerprint: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> AppState:
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


def state_path(project_dir: Path) -> Path:
    return project_dir / STATE_FILENAME


def _backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def _migrate(data: dict, path: Path) -> dict:
    version = data.get("schema_version", 1)
    if version > SCHEMA_VERSION:
        raise StateError(
            f"{path} has schema_version {version}, but this tool only knows "
            f"up to {SCHEMA_VERSION}. Upgrade teddycloudhelper."
        )
    if version < SCHEMA_VERSION:
        _backup_file(path)
        while version < SCHEMA_VERSION:
            try:
                migrate = MIGRATIONS[version]
            except KeyError:
                raise StateError(
                    f"No migration registered for schema_version {version} in {path}."
                ) from None
            data = migrate(data)
            version += 1
            data["schema_version"] = version
    return data


def load_state(project_dir: Path) -> AppState:
    """Load state from a project directory, migrating older schemas."""
    path = state_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StateError(f"No {STATE_FILENAME} found in {project_dir}.") from None
    except json.JSONDecodeError as exc:
        raise StateError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError(f"{path} does not contain a JSON object.")
    data = _migrate(data, path)
    return AppState.from_dict(data)


def save_state(state: AppState, project_dir: Path) -> Path:
    """Write state atomically (write to temp file, then rename)."""
    project_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(project_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def has_state(project_dir: Path) -> bool:
    return state_path(project_dir).is_file()


# --- Global pointer (last used project) -------------------------------------


# How many recently used projects to remember for the switcher.
RECENT_PROJECTS_CAP = 10


def _global_config_path() -> Path:
    return Path(user_config_dir("teddycloudhelper")) / "config.json"


def _load_global_config() -> dict:
    """The global config as a dict, or {} when missing/corrupt."""
    try:
        data = json.loads(_global_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_last_project() -> Path | None:
    """Return the last used project directory, or None if unknown/gone."""
    last = _load_global_config().get("last_project")
    if not last:
        return None
    project = Path(last)
    return project if project.is_dir() else None


def load_recent_projects() -> list[Path]:
    """Recently used project directories, newest first.

    Directories that no longer exist are silently dropped, so a stale entry
    never shows up in the switcher.
    """
    raw = _load_global_config().get("recent_projects", [])
    if not isinstance(raw, list):
        return []
    result: list[Path] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        project = Path(entry)
        if project.is_dir() and project not in result:
            result.append(project)
    return result


def save_last_project(project_dir: Path) -> None:
    """Record ``project_dir`` as the last used project and push it to the
    front of the recent-projects list (deduplicated, capped)."""
    resolved = project_dir.resolve()
    data = _load_global_config()

    # Seed the list from a legacy config that only had last_project, so an
    # upgraded install keeps its previous project in the switcher.
    previous = data.get("recent_projects")
    if not isinstance(previous, list):
        legacy = data.get("last_project")
        previous = [legacy] if isinstance(legacy, str) else []

    data["last_project"] = str(resolved)
    recent = [resolved]
    for entry in previous:
        if isinstance(entry, str) and Path(entry) != resolved:
            recent.append(Path(entry))
    data["recent_projects"] = [str(p) for p in recent[:RECENT_PROJECTS_CAP]]

    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
