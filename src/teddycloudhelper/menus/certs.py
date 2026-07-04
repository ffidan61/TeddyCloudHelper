"""Certificate submenu: WebUI CA + client certs, box CA export, box cert install."""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from teddycloudhelper import state as state_mod
from teddycloudhelper import ui
from teddycloudhelper.certs import box_certs, ca, client_certs, crl, server_certs
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.menus import project as project_menu

MENU_ACTIONS: list[tuple[str, str]] = [
    ("Create WebUI CA", "create_ca"),
    ("Issue a client certificate (browser access)", "issue"),
    ("List client certificates", "list"),
    ("Revoke a client certificate", "revoke"),
    ("Create self-signed WebUI server certificate", "server_cert"),
    ("Export box CA (ca.der) for flashing", "export_ca"),
    ("Install dumped box certificates", "box_certs"),
    ("Back to main menu", "back"),
]


def _create_ca(project: Path) -> None:
    cn = ui.ask_text("Common name for the CA:", default=ca.DEFAULT_CA_CN)
    path = ca.create_ca(project, common_name=cn)
    crl.ensure_crl(project)
    ui.info_panel(
        f"WebUI CA created: {path}\n"
        f"Empty CRL written: {crl.crl_path(project)}\n\n"
        "nginx will use both once the reverse-proxy templates are set up (v0.4).",
        title="CA created",
    )


def _issue(project: Path) -> None:
    name = ui.ask_text("Name for this certificate (e.g. the user or device):")
    password = ui.ask_password("Password for the .p12 bundle (empty = unprotected):")
    state = state_mod.load_state(project)
    info = client_certs.issue_client_cert(project, name, state.next_serial, password)
    state.next_serial += 1
    state_mod.save_state(state, project)
    ui.info_panel(
        f"Issued {info.name} (serial {info.serial}), "
        f"valid until {info.not_valid_after:%Y-%m-%d}.\n\n"
        f"Import this file into the browser: {info.p12_path}",
        title="Client certificate issued",
    )


def _list(project: Path) -> None:
    infos = client_certs.list_client_certs(project)
    if not infos:
        ui.info_panel("No client certificates issued yet.")
        return
    revoked = set(crl.revoked_serials(project))
    table = Table(title=f"Client certificates in {client_certs.clients_dir(project)}")
    table.add_column("Name")
    table.add_column("Serial")
    table.add_column("Expires")
    table.add_column("Status")
    for info in infos:
        status = "[red]revoked[/red]" if info.serial in revoked else "[green]valid[/green]"
        table.add_row(info.name, str(info.serial), f"{info.not_valid_after:%Y-%m-%d}", status)
    ui.console.print(table)


def _revoke(project: Path) -> None:
    revoked = set(crl.revoked_serials(project))
    candidates = [
        info for info in client_certs.list_client_certs(project) if info.serial not in revoked
    ]
    if not candidates:
        ui.info_panel("Nothing to revoke — no valid client certificates found.")
        return
    choices = [(f"{info.name} (serial {info.serial})", str(info.serial)) for info in candidates]
    serial = int(ui.menu("Revoke which certificate?", choices))
    if not ui.confirm(f"Revoke serial {serial}? This cannot be undone.", default=False):
        return
    path = crl.revoke_serial(project, serial)
    ui.info_panel(
        f"Serial {serial} revoked; CRL rewritten: {path}\n"
        "Restart/reload nginx so it picks up the new CRL.",
        title="Certificate revoked",
    )


def _server_cert(project: Path) -> None:
    state = state_mod.load_state(project)
    hostname = ui.ask_text(
        "Hostname or IP the WebUI is reached at:", default=state.webui_hostname
    )
    path = server_certs.create_webui_server_cert(project, hostname)
    if hostname != state.webui_hostname:
        state.webui_hostname = hostname
        state_mod.save_state(state, project)
    ui.info_panel(f"Self-signed WebUI certificate written: {path}")


def _export_ca(project: Path) -> None:
    dest = ui.ask_path("Export ca.der to (directory or file path):", default=str(project))
    path = server_certs.export_box_ca(project, dest)
    ui.info_panel(
        f"Box CA exported: {path}\n"
        "Flash this onto the Toniebox (replaces the Boxine CA).",
        title="ca.der exported",
    )


def _box_certs(project: Path) -> None:
    ui.info_panel(
        "Pick the three files dumped from the box (ca.der, client.der, private.der).\n"
        "They are irreplaceable — existing files are backed up before overwriting."
    )
    ca_der = ui.ask_path("Path to ca.der:", must_exist=True)
    client_der = ui.ask_path("Path to client.der:", must_exist=True)
    private_der = ui.ask_path("Path to private.der:", must_exist=True)
    info = box_certs.inspect_box_certs(ca_der, client_der, private_der)
    ui.console.print(
        f"Client cert: [bold]{info.client_common_name}[/bold] "
        f"(issuer {info.issuer}, valid until {info.not_valid_after:%Y-%m-%d})"
    )
    if not ui.confirm(f"Install into {project / 'certs' / 'client'}?", default=True):
        return
    installed = box_certs.install_box_certs(project, ca_der, client_der, private_der)
    ui.info_panel(
        "Installed:\n" + "\n".join(str(p) for p in installed),
        title="Box certificates installed",
    )


_HANDLERS = {
    "create_ca": _create_ca,
    "issue": _issue,
    "list": _list,
    "revoke": _revoke,
    "server_cert": _server_cert,
    "export_ca": _export_ca,
    "box_certs": _box_certs,
}


def run() -> None:
    """Submenu loop. Mirrors the main loop: errors render red and never crash."""
    project = project_menu.active_project()
    if project is None:
        return
    while True:
        ui.console.print(f"Active project: [bold]{project}[/bold]")
        try:
            action = ui.menu("Certificates", MENU_ACTIONS)
        except ui.Cancelled:
            return
        if action == "back":
            return
        try:
            _HANDLERS[action](project)
        except ui.Cancelled:
            continue
        except (CertError, state_mod.StateError) as exc:
            ui.error_panel(str(exc), title="Certificate error")
