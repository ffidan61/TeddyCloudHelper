"""Setup wizard: a sequence of small, individually testable step functions.

Each step mutates the :class:`~teddycloudhelper.state.AppState` (or prepares
files) and does nothing else; :func:`run` orchestrates them and finishes with
the standard cycle — save state, render templates (with ``.bak``), prompt for
(re)start.
"""

from __future__ import annotations

from pathlib import Path

from teddycloudhelper import docker_cli, render, ui
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import ca, crl, server_certs
from teddycloudhelper.state import AppState

COMPOSE_FILENAME = "docker-compose.yml"
NGINX_CONF_RELPATH = Path("nginx") / "nginx.conf"


def step_project_dir() -> Path:
    """Pick (and create) the project directory."""
    directory = ui.ask_path("Project directory for this TeddyCloud instance:").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def step_deployment_mode(state: AppState) -> None:
    state.deployment_mode = ui.menu(
        "How should TeddyCloud be exposed?",
        [
            ("Directly (TeddyCloud publishes its own ports; simplest)", "direct"),
            ("Behind nginx (SNI passthrough for the box, TLS-terminated WebUI)", "nginx"),
        ],
    )


def step_webui(state: AppState) -> None:
    """nginx mode only: hostname + how the WebUI is reached."""
    hostname = ""
    while not hostname:
        hostname = ui.ask_text(
            "Hostname the WebUI is reached at (must differ from the box hostnames):",
            default=state.webui_hostname or "teddycloud.local",
        ).strip()
    state.webui_hostname = hostname
    state.webui_port_mode = ui.menu(
        "Where should the WebUI listen?",
        [
            ("On its own port (default 8443)", "separate"),
            ("On 443, shared with the box via SNI split", "shared"),
        ],
    )
    if state.webui_port_mode == "separate":
        state.webui_port = _ask_port(state.webui_port or 8443)


def _ask_port(default: int) -> int:
    while True:
        raw = ui.ask_text("WebUI port:", default=str(default)).strip()
        try:
            port = int(raw)
        except ValueError:
            ui.error_panel(f"{raw!r} is not a number.")
            continue
        if 1 <= port <= 65535:
            return port
        ui.error_panel("Port must be between 1 and 65535.")


def step_webui_auth(state: AppState, project_dir: Path) -> None:
    """nginx mode only: TLS material for the WebUI + optional client-cert auth."""
    # Covers both a missing cert and a stale one after a hostname change.
    if not server_certs.webui_cert_matches(project_dir, state.webui_hostname):
        server_certs.create_webui_server_cert(project_dir, state.webui_hostname)
        ui.info_panel(
            f"Self-signed WebUI certificate for {state.webui_hostname} created in "
            f"{server_certs.server_dir(project_dir)}."
        )
    state.webui_client_cert_auth = ui.confirm(
        "Require client certificates for the WebUI (own CA, browser .p12)?",
        default=state.webui_client_cert_auth,
    )
    if state.webui_client_cert_auth and not ca.ca_exists(project_dir):
        ca.create_ca(project_dir)
        crl.ensure_crl(project_dir)
        ui.info_panel(
            "WebUI CA + empty CRL created. Issue client certificates via the "
            "certificate menu — without one in the browser, the WebUI is unreachable!"
        )


def render_project(state: AppState, project_dir: Path) -> list[Path]:
    """Render all config files for the chosen mode (existing files get a .bak)."""
    if state.deployment_mode == "nginx" and not state.webui_hostname:
        raise ValueError("nginx mode needs a WebUI hostname — run the setup wizard.")
    context = {
        "deployment_mode": state.deployment_mode,
        "webui_port_mode": state.webui_port_mode,
        "webui_hostname": state.webui_hostname,
        "webui_port": state.webui_port,
        "http_port": state.http_port,
        "webui_client_cert_auth": state.webui_client_cert_auth,
        "basic_auth_enabled": state.basic_auth_enabled,
        "ip_allowlist": state.ip_allowlist,
        "webui_tls_mode": state.webui_tls_mode,
        "letsencrypt_email": state.letsencrypt_email,
    }
    rendered = [
        render.render_to_file(
            "docker-compose.yml.j2", project_dir / COMPOSE_FILENAME, context
        )
    ]
    if state.deployment_mode == "nginx":
        rendered.append(
            render.render_to_file("nginx.conf.j2", project_dir / NGINX_CONF_RELPATH, context)
        )
    return rendered


def run() -> None:
    project_dir = step_project_dir()
    if state_mod.has_state(project_dir):
        state = state_mod.load_state(project_dir)
        ui.info_panel(
            "This directory is already a TeddyCloudHelper project — "
            "reconfiguring it (current values are preloaded)."
        )
    else:
        state = AppState()

    step_deployment_mode(state)
    if state.deployment_mode == "nginx":
        step_webui(state)
        step_webui_auth(state, project_dir)

    state_mod.save_state(state, project_dir)
    state_mod.save_last_project(project_dir)
    rendered = render_project(state, project_dir)
    ui.info_panel(
        "Configuration written:\n" + "\n".join(str(p) for p in rendered),
        title="Setup complete",
    )

    if ui.confirm("Start (or restart) the services now?", default=True):
        compose = docker_cli.Compose(project_dir)
        compose.up()
        ui.info_panel("Services started. TeddyCloud generates its server certs on "
                      "first start — export certs/server/ca.der via the certificate "
                      "menu afterwards to flash it onto the box.")
