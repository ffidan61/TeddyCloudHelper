"""Security submenu: Basic Auth users (htpasswd) and the IP allowlist.

Every change follows the standard cycle: update state → re-render templates
(with ``.bak``) → prompt for restart. Both features are enforced by nginx,
so in "direct" deployment mode they are stored but have no effect.
"""

from __future__ import annotations

from pathlib import Path

from teddycloudhelper import docker_cli, security, ui, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import ca, crl
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.menus import project as project_menu
from teddycloudhelper.security import SecurityError
from teddycloudhelper.state import AppState

MENU_ACTIONS: list[tuple[str, str]] = [
    ("Show security status", "status"),
    ("Enable / disable Basic Auth", "toggle_auth"),
    ("Require / stop requiring WebUI client certificates", "toggle_client_cert"),
    ("Add or update a Basic Auth user", "set_user"),
    ("Remove a Basic Auth user", "remove_user"),
    ("Add an IP allowlist entry", "add_ip"),
    ("Remove an IP allowlist entry", "remove_ip"),
    ("Let allowed IPs skip Basic Auth (satisfy any)", "toggle_bypass"),
    ("Back to project settings", "back"),
]


def _status(state: AppState, project: Path) -> None:
    users = security.load_users(project)
    lines = [
        f"Basic Auth: {'enabled' if state.basic_auth_enabled else 'disabled'}"
        f" ({len(users)} user(s): {', '.join(users) or '—'})",
        f"IP allowlist: {', '.join(state.ip_allowlist) or 'off (all IPs allowed)'}",
        f"WebUI client certificates: "
        f"{'required' if state.webui_client_cert_auth else 'not required'}",
    ]
    if state.basic_auth_enabled and state.ip_allowlist:
        lines.append(
            "Allowed IPs bypass Basic Auth: "
            + ("yes (satisfy any)" if state.ip_bypasses_basic_auth else "no (both required)")
        )
    if state.deployment_mode != "nginx":
        lines.append(
            "\n[bold red]Deployment mode is 'direct' — these settings are only "
            "enforced by nginx and currently have no effect![/bold red]"
        )
    ui.info_panel("\n".join(lines), title="Security status")


def _apply(state: AppState, project: Path) -> None:
    """The standard cycle: save state, re-render, offer restart."""
    state_mod.save_state(state, project)
    rendered = wizard.render_project(state, project)
    ui.console.print("Re-rendered: " + ", ".join(str(p) for p in rendered))
    if ui.confirm("Restart services now to apply the change?", default=True):
        _restart(project)


def _restart(project: Path) -> None:
    # Never restart nginx onto a broken config — it would take the box path
    # down with it. Rolls back to the last good .bak on failure.
    wizard.check_nginx_before_restart(project)
    # up + restart: the configs are bind-mounted, so when the compose
    # definition itself is unchanged, `up` leaves running containers alone
    # and nginx would keep serving the old config.
    compose = docker_cli.Compose(project)
    compose.up()
    compose.restart()


def _toggle_auth(state: AppState, project: Path) -> None:
    if state.basic_auth_enabled:
        if not ui.confirm("Basic Auth is enabled. Disable it?", default=False):
            return
        state.basic_auth_enabled = False
        # Stale otherwise: re-enabling Basic Auth later would silently bring
        # the bypass back with it.
        state.ip_bypasses_basic_auth = False
    else:
        if not security.load_users(project):
            ui.info_panel("No users yet — create the first one.")
            _prompt_set_user(project)
        state.basic_auth_enabled = True
    _apply(state, project)


def _toggle_client_cert(state: AppState, project: Path) -> None:
    if state.deployment_mode != "nginx":
        ui.error_panel(
            "Client-certificate auth is enforced by nginx — this project runs "
            "in 'direct' mode. Re-run the setup wizard to switch to nginx mode."
        )
        return
    if state.webui_client_cert_auth:
        if not ui.confirm(
            "Client certificates are currently REQUIRED for the WebUI. "
            "Stop requiring them?",
            default=False,
        ):
            return
        state.webui_client_cert_auth = False
    else:
        if not ui.confirm(
            "Require browser client certificates (mTLS, own CA) for the WebUI?",
            default=True,
        ):
            return
        state.webui_client_cert_auth = True
        if not ca.ca_exists(project):
            ca.create_ca(project)
            crl.ensure_crl(project)
            ui.info_panel("WebUI CA + empty CRL created.")
        wizard.step_first_client_cert(state, project)
    _apply(state, project)


def _toggle_bypass(state: AppState, project: Path) -> None:
    if not (state.basic_auth_enabled and state.ip_allowlist):
        ui.error_panel(
            "This needs both Basic Auth and at least one IP allowlist entry "
            "enabled first — otherwise there is nothing to bypass."
        )
        return
    if state.ip_bypasses_basic_auth:
        state.ip_bypasses_basic_auth = False
        ui.info_panel("Allowed IPs now also need the password again (both required).")
    else:
        if not ui.confirm(
            "Allowed IPs will get in WITHOUT a password (satisfy any). "
            "Everyone else still needs one. Continue?",
            default=False,
        ):
            return
        state.ip_bypasses_basic_auth = True
    _apply(state, project)


def _prompt_set_user(project: Path) -> None:
    username = ui.ask_text("Username:")
    password = ui.ask_password("Password:")
    security.set_user(project, username, password)
    ui.info_panel(f"User {username!r} written to {security.htpasswd_path(project)}.")


def _set_user(state: AppState, project: Path) -> None:
    _prompt_set_user(project)
    if state.basic_auth_enabled:
        ui.info_panel("htpasswd changed — nginx picks it up on the next restart.")
        if ui.confirm("Restart services now?", default=True):
            _restart(project)


def _remove_user(state: AppState, project: Path) -> None:
    users = security.load_users(project)
    if not users:
        ui.info_panel("No Basic Auth users exist.")
        return
    username = ui.menu("Remove which user?", [(u, u) for u in users])
    if len(users) == 1 and state.basic_auth_enabled:
        ui.error_panel(
            f"{username!r} is the last user while Basic Auth is enabled — "
            "removing it would lock everyone out. Disable Basic Auth first."
        )
        return
    security.remove_user(project, username)
    ui.info_panel(f"User {username!r} removed.")


def _add_ip(state: AppState, project: Path) -> None:
    entry = security.normalize_allowlist_entry(
        ui.ask_text("IP address or network (e.g. 192.168.0.0/24):")
    )
    if entry in state.ip_allowlist:
        ui.info_panel(f"{entry} is already on the allowlist.")
        return
    if not state.ip_allowlist:
        ui.info_panel(
            "This is the first entry — from now on, ALL other IPs are denied "
            "access to the web ports. Make sure your own address is covered!"
        )
    state.ip_allowlist.append(entry)
    _apply(state, project)


def _remove_ip(state: AppState, project: Path) -> None:
    if not state.ip_allowlist:
        ui.info_panel("The IP allowlist is empty.")
        return
    entry = ui.menu(
        "Remove which entry?", [(e, e) for e in state.ip_allowlist]
    )
    state.ip_allowlist.remove(entry)
    if not state.ip_allowlist:
        ui.info_panel("Allowlist is now empty — all IPs are allowed again.")
        # Stale otherwise: adding a new entry later would silently bring the
        # bypass back with it.
        state.ip_bypasses_basic_auth = False
    _apply(state, project)


_HANDLERS = {
    "status": _status,
    "toggle_auth": _toggle_auth,
    "toggle_client_cert": _toggle_client_cert,
    "set_user": _set_user,
    "remove_user": _remove_user,
    "add_ip": _add_ip,
    "remove_ip": _remove_ip,
    "toggle_bypass": _toggle_bypass,
}


def run() -> None:
    project = project_menu.active_project()
    if project is None:
        return
    while True:
        ui.console.print(f"Active project: [bold]{project}[/bold]")
        try:
            action = ui.menu("Security", MENU_ACTIONS)
        except ui.Cancelled:
            return
        if action == "back":
            return
        try:
            state = state_mod.load_state(project)
            _HANDLERS[action](state, project)
        except ui.Cancelled:
            continue
        except (
            SecurityError,
            CertError,
            state_mod.StateError,
            docker_cli.DockerError,
            ValueError,
        ) as exc:
            ui.error_panel(str(exc), title="Security error")
