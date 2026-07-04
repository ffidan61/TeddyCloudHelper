"""Thin wrapper around the ``docker compose`` CLI.

Every invocation goes through an injectable *runner* so tests can fake the
subprocess layer without Docker; nothing here ever uses ``shell=True``.
Container status is always read live via ``compose ps`` — never cached or
persisted (see CLAUDE.md).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Names docker compose looks for, in its own precedence order.
COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
)

# runner(args, cwd) -> CompletedProcess; replaced by a fake in tests.
Runner = Callable[[list[str], Path], subprocess.CompletedProcess]


class DockerError(Exception):
    """A docker/compose invocation failed."""


def find_compose_file(directory: Path) -> Path | None:
    """Return the compose file docker compose would pick in *directory*."""
    for name in COMPOSE_FILENAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _default_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        raise DockerError(f"Could not run {args[0]!r}: {exc}") from exc


@dataclass
class ServiceStatus:
    """One row of ``docker compose ps`` output."""

    name: str
    service: str
    state: str  # e.g. "running", "exited"
    status: str  # human-readable, e.g. "Up 2 hours (healthy)"
    health: str = ""


def _parse_ps_output(stdout: str) -> list[ServiceStatus]:
    # Compose >= 2.21 emits one JSON object per line; older versions a JSON array.
    text = stdout.strip()
    if not text:
        return []
    try:
        if text.startswith("["):
            entries = json.loads(text)
        else:
            entries = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise DockerError(f"Could not parse `docker compose ps` output: {exc}") from exc
    return [
        ServiceStatus(
            name=entry.get("Name", ""),
            service=entry.get("Service", ""),
            state=entry.get("State", ""),
            status=entry.get("Status", ""),
            health=entry.get("Health", ""),
        )
        for entry in entries
    ]


class Compose:
    """``docker compose`` operations for one project directory."""

    def __init__(self, project_dir: Path, runner: Runner | None = None) -> None:
        self.project_dir = project_dir
        self._runner = runner or _default_runner

    def _run(self, *compose_args: str) -> subprocess.CompletedProcess:
        args = ["docker", "compose", *compose_args]
        result = self._runner(args, self.project_dir)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise DockerError(
                f"`{' '.join(args)}` failed with exit code {result.returncode}"
                + (f":\n{detail}" if detail else ".")
            )
        return result

    def ps(self) -> list[ServiceStatus]:
        """Live container status, including stopped containers."""
        return _parse_ps_output(self._run("ps", "--all", "--format", "json").stdout)

    def up(self) -> None:
        # --remove-orphans: reconfiguring can drop services (e.g. nginx when
        # switching to direct mode); their old containers must not linger.
        self._run("up", "--detach", "--remove-orphans")

    def stop(self) -> None:
        self._run("stop")

    def restart(self) -> None:
        self._run("restart")

    def pull(self) -> None:
        self._run("pull")

    def run_service(self, service: str, *args: str) -> subprocess.CompletedProcess:
        """One-off ``compose run --rm <service> <args…>`` (e.g. certbot certonly)."""
        return self._run("run", "--rm", service, *args)

    def logs(self, tail: int = 100) -> str:
        result = self._run("logs", "--no-color", "--tail", str(tail))
        return result.stdout
