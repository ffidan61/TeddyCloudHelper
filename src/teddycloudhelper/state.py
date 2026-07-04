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

SCHEMA_VERSION = 1
STATE_FILENAME = "teddycloudhelper.json"

# Migration i transforms a raw dict from schema_version i to i+1.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


class StateError(Exception):
    """Raised when a state file cannot be read or migrated."""


@dataclass
class AppState:
    """User-chosen configuration for one TeddyCloud project."""

    schema_version: int = SCHEMA_VERSION
    # "direct" (TeddyCloud exposes its ports itself) or "nginx" (reverse proxy).
    deployment_mode: str = "direct"
    # "shared" (WebUI and box share 443 via SNI split) or "separate" (own port).
    webui_port_mode: str = "separate"
    box_hostname: str = ""
    webui_hostname: str = ""
    webui_port: int = 8443
    http_port: int = 80
    # Require browser client certs (own CA, mTLS at nginx) for the WebUI.
    webui_client_cert_auth: bool = False
    # "selfsigned" (webui-pki/server/) or "letsencrypt" (certbot-managed).
    webui_tls_mode: str = "selfsigned"
    # Non-empty enables the certbot side-container in the compose file.
    letsencrypt_email: str = ""
    basic_auth_enabled: bool = False
    ip_allowlist: list[str] = field(default_factory=list)
    # Monotone serial counter for issued client certificates.
    next_serial: int = 1

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


def _global_config_path() -> Path:
    return Path(user_config_dir("teddycloudhelper")) / "config.json"


def load_last_project() -> Path | None:
    """Return the last used project directory, or None if unknown/gone."""
    path = _global_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    last = data.get("last_project") if isinstance(data, dict) else None
    if not last:
        return None
    project = Path(last)
    return project if project.is_dir() else None


def save_last_project(project_dir: Path) -> None:
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_project": str(project_dir.resolve())}, indent=2) + "\n",
        encoding="utf-8",
    )
