"""WebUI security helpers: htpasswd (bcrypt) and IP-allowlist validation.

Both are enforced by nginx, so they only take effect in nginx deployment
mode — the security menu warns about that. The htpasswd file lives in
``<project>/security/htpasswd`` and is mounted read-only into the nginx
container; allowlist entries are stored in ``AppState.ip_allowlist`` and
rendered as ``allow``/``deny`` rules.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import bcrypt

SECURITY_DIRNAME = "security"
HTPASSWD_FILENAME = "htpasswd"

# No ':' (field separator), no whitespace/control characters.
_USERNAME_RE = re.compile(r"^[^:\s]+$")


class SecurityError(Exception):
    """A security-configuration operation failed."""


def htpasswd_path(project_dir: Path) -> Path:
    return project_dir / SECURITY_DIRNAME / HTPASSWD_FILENAME


def load_users(project_dir: Path) -> list[str]:
    """Usernames in the htpasswd file, read live. Empty if none exists."""
    try:
        lines = htpasswd_path(project_dir).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line.split(":", 1)[0] for line in lines if ":" in line]


def set_user(project_dir: Path, username: str, password: str) -> Path:
    """Add or update *username* with a bcrypt hash of *password*."""
    if not _USERNAME_RE.match(username):
        raise SecurityError(
            f"Invalid username {username!r}: no spaces or ':' allowed."
        )
    if not password:
        raise SecurityError("Password must not be empty.")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    path = htpasswd_path(project_dir)
    lines = _other_lines(path, username)
    lines.append(f"{username}:{hashed}")
    _write(path, lines)
    return path


def remove_user(project_dir: Path, username: str) -> bool:
    """Remove *username*; returns False if it was not present."""
    path = htpasswd_path(project_dir)
    before = load_users(project_dir)
    if username not in before:
        return False
    _write(path, _other_lines(path, username))
    return True


def _other_lines(path: Path, username: str) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line.split(":", 1)[0] != username]


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # NOTE: must stay world-readable (0644 default) — the nginx *worker*
    # (uid 101 in the container) reads this file per request through the ro
    # bind mount, and it is neither the owner nor in the owner's group.
    # bcrypt hashes are the protection here, not file permissions.
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


# --- IP allowlist --------------------------------------------------------------


def normalize_allowlist_entry(entry: str) -> str:
    """Validate an IP or CIDR entry and return its canonical form.

    Accepts single addresses (``192.168.1.5``) and networks
    (``192.168.0.0/24``); host bits in networks are tolerated.
    """
    entry = entry.strip()
    if not entry:
        raise SecurityError("Entry must not be empty.")
    try:
        network = ipaddress.ip_network(entry, strict=False)
    except ValueError as exc:
        raise SecurityError(f"{entry!r} is not a valid IP address or network: {exc}") from exc
    # Render single hosts without the redundant prefix (nginx accepts both,
    # but `192.168.1.5` reads better than `192.168.1.5/32`).
    if network.num_addresses == 1:
        return str(network.network_address)
    return str(network)
