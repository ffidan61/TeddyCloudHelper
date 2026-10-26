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
    ("Switch TeddyCloud image channel (latest / develop)", "image"),
    ("Run TeddyCloud as non-root (PUID/PGID)", "run_as"),
    ("Switch deployment mode (direct / nginx)", "mode"),
    ("Security (Basic Auth, client certificates, IP allowlist)", "security"),
    ("Re-render config files (after a TeddyCloudHelper update)", "rerender"),
    ("Back to main menu", "back"),
]


def _show(state: AppState, project: Path) -> None:
    lines = [
        f"Deployment mode:   {state.deployment_mode}",
        f"Image channel:     {state.teddycloud_image_tag}",
        "Runs as:           "
        + (f"UID {state.puid} / GID {state.pgid}" if state.puid else "root"),
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
    raw = ui.ask_text("WebUI hostname:", default=state.webui_hostname).strip()
    if not raw or raw == state.webui_hostname:
        ui.info_panel("Hostname unchanged — nothing to do.")
        return
    new = server_certs.validate_webui_hostname(raw)
    if new == state.webui_hostname:
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


def _foreign_compose_ok(project: Path) -> bool:
    """True when re-rendering may proceed: the compose file is ours (or
    absent), or the user confirmed replacing their hand-written one."""
    compose_file = docker_cli.find_compose_file(project)
    if compose_file is None or "Generated by TeddyCloudHelper" in (
        compose_file.read_text(encoding="utf-8", errors="replace")
    ):
        return True
    return ui.confirm(
        f"{compose_file.name} was not generated by this tool — continuing "
        "re-renders it from the template and REPLACES your custom file "
        "(a timestamped .bak is written). Continue?",
        default=False,
    )


def _image_channel(state: AppState, project: Path) -> None:
    tag = ui.menu(
        f"Current channel: {state.teddycloud_image_tag} — switch to?",
        [
            ("latest — stable releases (recommended)", "latest"),
            ("develop — newest features, may break", "develop"),
        ],
    )
    if tag == state.teddycloud_image_tag:
        ui.info_panel(f"Already on {tag!r} — nothing to do.")
        return
    if not _foreign_compose_ok(project):
        return
    state.teddycloud_image_tag = tag
    state_mod.save_state(state, project)
    wizard.render_project(state, project)
    ui.info_panel(f"Compose file now uses the {tag!r} image.")
    # Not the shared _apply: the new image must be pulled before the restart.
    if ui.confirm("Pull the image and restart now?", default=True):
        docker_cli.Compose(project).pull()
        wizard.restart_services(project)


def _run_as(state: AppState, project: Path) -> None:
    """Toggle the upstream PUID/PGID non-root support (TeddyCloud >= v0.7.0)."""
    current = f"UID {state.puid} / GID {state.pgid}" if state.puid else "root"
    choice = ui.menu(
        f"TeddyCloud currently runs as {current} — change to?",
        [
            ("Non-root: run as a host user via PUID/PGID (recommended)", "nonroot"),
            ("Root — the pre-v0.7.0 behaviour", "root"),
        ],
    )
    if choice == "root":
        if not state.puid:
            ui.info_panel("Already running as root — nothing to do.")
            return
        state.puid = 0
        state.pgid = 0
        ui.info_panel(
            "Back to root. Files chowned by an earlier non-root start keep "
            "their ownership — the root process can read them regardless."
        )
    else:
        uid, gid = (state.puid, state.pgid) if state.puid else wizard.default_ids()
        puid, pgid = wizard.ask_ids(uid, gid)
        if (puid, pgid) == (state.puid, state.pgid):
            ui.info_panel(f"Already running as UID {puid} / GID {pgid} — nothing to do.")
            return
        state.puid, state.pgid = puid, pgid
        ui.warn_panel(
            "Needs the TeddyCloud v0.7.0+ image (older images ignore the "
            "variables and stay root — pull first if unsure). On every "
            "container start the entrypoint runs `chown -R` over /teddycloud, "
            "so ownership of the project's data directories on the host "
            "changes to this UID/GID; the first start can take a while with "
            "a large library.",
            title="Non-root mode",
        )
    if not _foreign_compose_ok(project):
        return
    _apply(state, project)


def _mode(state: AppState, project: Path) -> None:
    if state.deployment_mode == "direct":
        _switch_to_nginx(state, project)
        return
    if not ui.confirm(
        "Switch to 'direct' mode? nginx is removed — Basic Auth, client "
        "certificates, the IP allowlist and Let's Encrypt stop working "
        "(TeddyCloud publishes its ports itself).",
        default=False,
    ):
        return
    state.deployment_mode = "direct"
    if wizard.disable_letsencrypt_without_nginx(state):
        ui.info_panel(wizard.LETSENCRYPT_RESET_NOTE, title="Let's Encrypt disabled")
    _apply(state, project)


def _switch_to_nginx(state: AppState, project: Path) -> None:
    """Move a direct deployment behind nginx by running the wizard's nginx
    steps (hostname, WebUI cert, access protection) — same functions the setup
    wizard uses, so no logic is duplicated."""
    if not ui.confirm(
        "Switch to 'nginx' mode? This walks through the WebUI hostname, "
        "certificates and access protection, then re-renders and restarts.",
        default=True,
    ):
        return
    wizard.step_webui(state)
    wizard.step_webui_auth(state, project)
    wizard.step_first_client_cert(state, project)
    wizard.step_protection(state, project)
    # Let's Encrypt is intentionally left for the certificate menu's
    # "Set up Let's Encrypt" action: its issuance is a multi-phase flow
    # (enable certbot, run certonly through the webroot, switch nginx over)
    # that does not fit this menu's simple "save → re-render → restart" cycle.
    state.deployment_mode = "nginx"
    _apply(state, project)


def _rerender(state: AppState, project: Path) -> None:
    """Regenerate compose + nginx configs from the current templates.

    The escape hatch when a TeddyCloudHelper update changed a template (a
    new mount, an nginx directive): existing deployments keep their old
    rendered files until this runs. The doctor's mount check points here —
    which for an ADOPTED install means a hand-written compose file, so the
    foreign check must ask before replacing it.
    """
    if not _foreign_compose_ok(project):
        return
    _apply(state, project)


_HANDLERS = {
    "show": _show,
    "hostname": _hostname,
    "port_mode": _port_mode,
    "image": _image_channel,
    "run_as": _run_as,
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
