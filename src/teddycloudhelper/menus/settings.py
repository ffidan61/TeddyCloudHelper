"""Project settings submenu: adjust single options without the full wizard.

Every change follows the standard cycle: update state → re-render templates
(with ``.bak``) → prompt for restart. Security options live in the security
menu, the image channel in the docker menu — this menu covers the deployment
options that previously required a full wizard run.
"""

from __future__ import annotations

from pathlib import Path

from teddycloudhelper import docker_cli, ui, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import letsencrypt, server_certs
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.menus import project as project_menu
from teddycloudhelper.menus import security as security_menu
from teddycloudhelper.state import AppState

MENU_ACTIONS: list[tuple[str, str]] = [
    ("Show current configuration", "show"),
    ("Change WebUI hostname", "hostname"),
    ("Change where the WebUI listens (own port / shared 443)", "port_mode"),
    ("Switch deployment mode (direct / nginx)", "mode"),
    ("Security (Basic Auth, client certificates, IP allowlist)", "security"),
    ("Re-render config files (after a TeddyCloudHelper update)", "rerender"),
    ("Back to main menu", "back"),
]


def _show(state: AppState, project: Path) -> None:
    lines = [
        f"Deployment mode:   {state.deployment_mode}",
        f"Image channel:     {state.teddycloud_image_tag}",
        f"WebUI hostname:    {state.webui_hostname or '—'}",
        "WebUI listens on:  "
        + (
            f"port {state.webui_port}"
            if state.webui_port_mode == "separate"
            else "443 (shared with the box via SNI)"
        ),
        f"WebUI TLS:         {state.webui_tls_mode}",
        f"Basic Auth:        {'enabled' if state.basic_auth_enabled else 'disabled'}",
        f"Client certs:      "
        f"{'required' if state.webui_client_cert_auth else 'not required'}",
        f"IP allowlist:      {', '.join(state.ip_allowlist) or 'off'}",
    ]
    ui.info_panel("\n".join(lines), title=f"Configuration of {project}")


def _apply(state: AppState, project: Path) -> None:
    """The standard cycle: save state, re-render, offer restart."""
    state_mod.save_state(state, project)
    rendered = wizard.render_project(state, project)
    ui.console.print("Re-rendered: " + ", ".join(str(p) for p in rendered))
    if ui.confirm("Restart services now to apply the change?", default=True):
        wizard.restart_services(project)


def _hostname(state: AppState, project: Path) -> None:
    new = ui.ask_text("WebUI hostname:", default=state.webui_hostname).strip()
    if not new or new == state.webui_hostname:
        ui.info_panel("Hostname unchanged — nothing to do.")
        return
    state.webui_hostname = new
    if state.deployment_mode == "nginx":
        # Never re-request a certificate that is already on disk — check
        # the files instead of asking.
        if state.webui_tls_mode == "letsencrypt" and not letsencrypt.cert_exists(
            project, new
        ):
            # Pointing nginx at a non-existent live/<newname>/ directory
            # would keep it from starting, so fall back to self-signed
            # until LE is re-run for the new name.
            state.webui_tls_mode = "selfsigned"
            ui.warn_panel(
                "The Let's Encrypt certificate belongs to the old hostname — "
                "the WebUI falls back to a self-signed certificate. Re-run "
                "'Set up Let's Encrypt' from the certificate menu for the "
                "new name.",
                title="Let's Encrypt reset",
            )
        if not server_certs.webui_cert_matches(project, new):
            server_certs.create_webui_server_cert(project, new)
            ui.info_panel(f"Self-signed WebUI certificate for {new} created.")
    _apply(state, project)


def _port_mode(state: AppState, project: Path) -> None:
    mode = ui.menu(
        "Where should the WebUI listen?",
        [
            ("On its own port, default 8443 (recommended)", "separate"),
            (
                "On 443, shared with the box via SNI split "
                "(advanced — the box then needs its OWN hostname)",
                "shared",
            ),
        ],
    )
    if mode == "separate":
        state.webui_port = wizard._ask_port(state.webui_port or 8443)
    elif state.webui_port_mode != "shared":
        ui.warn_panel(
            "On shared 443, a box whose firmware is patched with the WebUI "
            "hostname cannot connect — give the box its own DNS name.",
            title="Shared port 443",
        )
    if mode == state.webui_port_mode and mode == "shared":
        ui.info_panel("Already sharing port 443 — nothing to do.")
        return
    state.webui_port_mode = mode
    _apply(state, project)


def _mode(state: AppState, project: Path) -> None:
    if state.deployment_mode == "direct":
        ui.error_panel(
            "Switching to nginx mode needs the full setup (hostname, WebUI "
            "certificates) — run the setup wizard instead."
        )
        return
    if not ui.confirm(
        "Switch to 'direct' mode? nginx is removed — Basic Auth, client "
        "certificates, the IP allowlist and Let's Encrypt stop working "
        "(TeddyCloud publishes its ports itself).",
        default=False,
    ):
        return
    state.deployment_mode = "direct"
    _apply(state, project)


def _rerender(state: AppState, project: Path) -> None:
    """Regenerate compose + nginx configs from the current templates.

    The escape hatch when a TeddyCloudHelper update changed a template (a
    new mount, an nginx directive): existing deployments keep their old
    rendered files until this runs. The doctor's mount check points here.
    """
    _apply(state, project)


_HANDLERS = {
    "show": _show,
    "hostname": _hostname,
    "port_mode": _port_mode,
    "mode": _mode,
    "rerender": _rerender,
}


def run() -> None:
    project = project_menu.active_project()
    if project is None:
        return
    # Show the current values up front — deciding what to change starts
    # with knowing what is set.
    try:
        _show(state_mod.load_state(project), project)
    except state_mod.StateError as exc:
        ui.error_panel(str(exc), title="Settings error")
    while True:
        ui.console.print(f"Active project: [bold]{project}[/bold]")
        try:
            action = ui.menu("Project settings", MENU_ACTIONS)
        except ui.Cancelled:
            return
        if action == "back":
            return
        if action == "security":
            # Delegate to the security submenu (own loop, own error handling).
            security_menu.run()
            continue
        try:
            state = state_mod.load_state(project)
            _HANDLERS[action](state, project)
        except ui.Cancelled:
            continue
        except (
            CertError,
            state_mod.StateError,
            docker_cli.DockerError,
            ValueError,
        ) as exc:
            ui.error_panel(str(exc), title="Settings error")
