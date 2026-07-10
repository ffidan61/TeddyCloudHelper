"""Best-effort update hint from the project's GitHub tags.

Installation is via git URL (no PyPI), so the only signal that a newer
version exists is a tag on the repo. The anonymous GitHub API answers 404
while the repo is private, so ``git ls-remote`` over the same SSH URL the
tool is installed from serves as the fallback — any machine that could
install the tool has those credentials. Startup must never hang or crash
over this — every call has a short timeout and swallows all errors,
returning ``None`` (no notice) on any failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

# The tool's own repository; its tags are pushed as vX.Y.Z (see the
# version-bump routine).
HELPER_REPO = "ffidan61/TeddyCloudHelper"
HELPER_GIT_URL = "git@github.com:ffidan61/TeddyCloudHelper.git"

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


def _tag_names_from_api(repo: str) -> list[str] | None:
    try:
        tags = _get_json(f"https://api.github.com/repos/{repo}/tags")
        return [entry.get("name", "") for entry in tags]
    except (OSError, ValueError):
        return None


def _tag_names_from_git(url: str) -> list[str] | None:
    """Tag names via ``git ls-remote`` — works on the private repo because
    it uses the same SSH credentials the tool was installed with."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            capture_output=True,
            text=True,
            timeout=5,
            env=os.environ
            | {
                # Never let git or ssh stop to ask anything at startup.
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=3",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    names = []
    for line in result.stdout.splitlines():
        ref = line.partition("\t")[2]
        # Annotated tags appear twice, once dereferenced as "<tag>^{}".
        name = ref.removeprefix("refs/tags/").removesuffix("^{}")
        if name:
            names.append(name)
    return names


def latest_helper_tag(
    repo: str = HELPER_REPO, git_url: str = HELPER_GIT_URL
) -> str | None:
    """Newest ``vX.Y.Z`` tag of the repo, or None on any failure."""
    names = _tag_names_from_api(repo)
    if names is None:
        names = _tag_names_from_git(git_url)
    if names is None:
        return None
    best_tag: str | None = None
    best_version: tuple[int, ...] | None = None
    for name in names:
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
        f"TeddyCloudHelper update available: {latest} (you have v{installed}). "
        "Run 'uv tool upgrade teddycloudhelper' to update. This is about the "
        "CLI tool itself, not the TeddyCloud server — see the doctor's "
        "'TeddyCloud image freshness' check for that."
    )
