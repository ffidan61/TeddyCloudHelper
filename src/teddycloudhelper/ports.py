"""Host port checks, run before containers are created or started.

A connect test against localhost is used instead of a bind test: binding
ports below 1024 needs privileges the tool usually doesn't have, while
connecting never does. It is a heuristic — a service bound to one specific
non-loopback interface can slip through — but it catches the common case
(another web server already on 80/443) with a clear message instead of a
cryptic docker error.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable

from teddycloudhelper.state import AppState


def required_ports(state: AppState) -> list[int]:
    """The host ports this project publishes, per deployment mode."""
    ports = [state.http_port, 443]
    if state.deployment_mode == "direct" or state.webui_port_mode == "separate":
        ports.append(state.webui_port)
    return list(dict.fromkeys(ports))  # dedupe, keep order


def check_ports(candidates: Iterable[int]) -> list[int]:
    """Return the subset of *candidates* something on this host listens on."""
    busy = []
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    return busy
