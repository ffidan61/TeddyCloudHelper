"""Best-effort update hint from the project's GitHub tags.

Installation is via git URL (no PyPI), so the only signal that a newer
version exists is a tag on the repo. Startup must never hang or crash over
this — every call has a short timeout and swallows all errors, returning
``None`` (no notice) on any failure.
"""

from __future__ import annotations

import json
import urllib.request

# The tool's own repository; its tags are pushed as vX.Y.Z (see the
# version-bump routine).
HELPER_REPO = "ffidan61/TeddyCloudHelper"

_TIMEOUT = 2.0


def _get_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TeddyCloudHelper",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def _parse_version(tag: str) -> tuple[int, ...] | None:
    """``"v0.11.1"`` -> ``(0, 11, 1)``; None when it is not a plain version."""
    core = tag.lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    try:
        return tuple(int(part) for part in core.split("."))
    except ValueError:
        return None


def latest_helper_tag(repo: str = HELPER_REPO) -> str | None:
    """Newest ``vX.Y.Z`` tag of the repo, or None on any failure."""
    try:
        tags = _get_json(f"https://api.github.com/repos/{repo}/tags")
    except (OSError, ValueError):
        return None
    best_tag: str | None = None
    best_version: tuple[int, ...] | None = None
    for entry in tags:
        name = entry.get("name", "")
        version = _parse_version(name)
        if version is not None and (best_version is None or version > best_version):
            best_version, best_tag = version, name
    return best_tag


def update_notice(installed: str, repo: str = HELPER_REPO) -> str | None:
    """A one-line 'newer version available' notice, or None when the
    installed version is current or nothing could be determined."""
    latest = latest_helper_tag(repo)
    if latest is None:
        return None
    latest_version = _parse_version(latest)
    installed_version = _parse_version(installed)
    if (
        latest_version is None
        or installed_version is None
        or latest_version <= installed_version
    ):
        return None
    return (
        f"Update available: {latest} (installed v{installed}). "
        "Reinstall from the git URL to upgrade."
    )
