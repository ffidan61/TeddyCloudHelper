"""Setup wizard: a sequence of small, individually testable step functions.

Each step mutates the :class:`~teddycloudhelper.state.AppState` (or prepares
files) and does nothing else; :func:`run` orchestrates them and finishes with
the standard cycle — save state, render templates (with ``.bak``), prompt for
(re)start.
"""

from __future__ import annotations

from pathlib import Path

from teddycloudhelper import docker_cli, render, security, ui
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import ca, client_certs, crl, letsencrypt, server_certs
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.menus import project as project_menu
from teddycloudhelper.state import AppState

COMPOSE_FILENAME = "docker-compose.yml"
NGINX_CONF_RELPATH = Path("nginx") / "nginx.conf"

# Bind-mount sources of the teddycloud service (upstream mounts them under
# /teddycloud/data/…). Created before docker does, so they are owned by the
# invoking user instead of root.
DATA_DIRS = (
    "certs",
    "config",
    "content",
    "library",
    "custom_img",
    "firmware",
    "cache",
    "plugins",
)


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


def step_image_tag(state: AppState) -> None:
    state.teddycloud_image_tag = ui.menu(
        "Which TeddyCloud image channel?",
        [
            ("latest — stable releases (recommended)", "latest"),
            ("develop — newest features, may break", "develop"),
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
            ("On its own port, default 8443 (recommended)", "separate"),
            (
                "On 443, shared with the box via SNI split "
                "(advanced — the box then needs its OWN hostname)",
                "shared",
            ),
        ],
    )
    if state.webui_port_mode == "separate":
        state.webui_port = _ask_port(state.webui_port or 8443)
    else:
        ui.warn_panel(
            "Shared 443 routes by TLS server name (SNI): connections using "
            f"{state.webui_hostname!r} go to the WebUI, everything else to "
            "the box endpoint. A box whose firmware is patched with the "
            "WebUI hostname CANNOT connect — give the box its own DNS name "
            "(same IP) and use that when patching the firmware.",
            title="Shared port 443",
        )


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
    if state.webui_client_cert_auth:
        # Already configured — don't re-ask on every reconfiguration; the
        # security menu has the toggle to turn it off.
        ui.info_panel(
            "Client-certificate auth stays enabled (change it in the "
            "security menu)."
        )
    else:
        state.webui_client_cert_auth = ui.confirm(
            "Require client certificates for the WebUI (own CA, browser .p12)?",
            default=False,
        )
    if state.webui_client_cert_auth and not ca.ca_exists(project_dir):
        ca.create_ca(project_dir)
        crl.ensure_crl(project_dir)
        ui.info_panel("WebUI CA + empty CRL created.")


def step_first_client_cert(state: AppState, project_dir: Path) -> None:
    """Offer the first browser cert right away — without one the WebUI locks
    the user out as soon as client-cert auth goes live."""
    if not state.webui_client_cert_auth or client_certs.list_client_certs(project_dir):
        return
    if not ui.confirm(
        "Create the first client certificate now? (Without one imported in "
        "your browser, the WebUI will be unreachable once nginx starts.)",
        default=True,
    ):
        ui.info_panel(
            "Remember to issue one via the certificate menu BEFORE relying on "
            "the WebUI — client-cert auth locks everyone out otherwise."
        )
        return
    while True:
        name = ui.ask_text("Name for this certificate:", default="admin")
        password = ui.ask_password("Password for the .p12 bundle (empty = unprotected):")
        try:
            info = client_certs.issue_client_cert(
                project_dir, name, state.next_serial, password
            )
            break
        except CertError as exc:
            ui.error_panel(str(exc))
    state.next_serial += 1
    state_mod.save_state(state, project_dir)
    ui.info_panel(
        f"Issued {info.name} (serial {info.serial}).\n\n"
        f"Import this file into your browser: {info.p12_path}",
        title="Client certificate issued",
    )


def step_protection(state: AppState, project_dir: Path) -> None:
    """nginx mode: never leave the WebUI wide open. An unprotected public
    WebUI trips TeddyCloud's security-mitigation lock (which also cuts off
    the boxes) and exposes the irreplaceable box certificates."""
    if state.basic_auth_enabled or state.webui_client_cert_auth or state.ip_allowlist:
        return
    ui.error_panel(
        "The WebUI currently has NO access protection. Once internet "
        "scanners find it, TeddyCloud locks itself (the boxes stop working "
        "too) and anybody could download your box certificates.",
        title="WebUI unprotected",
    )
    if not ui.confirm("Enable Basic Auth (username/password) now?", default=True):
        ui.info_panel(
            "OK — consider client certificates or the IP allowlist in the "
            "security menu before exposing the WebUI to the internet."
        )
        return
    username = ui.ask_text("Username:")
    password = ui.ask_password("Password:")
    security.set_user(project_dir, username, password)
    state.basic_auth_enabled = True
    ui.info_panel(f"Basic Auth enabled; user {username!r} created.")


def step_letsencrypt(state: AppState) -> str | None:
    """nginx mode: Let's Encrypt is the default when the hostname is public.

    Returns the validated hostname when the user wants LE, else None.
    No email is asked for — LE no longer sends expiry notifications, so
    certbot registers without one and this tool does the expiry monitoring.
    """
    try:
        hostname = letsencrypt.validate_hostname(state.webui_hostname)
    except CertError:
        ui.info_panel(
            f"{state.webui_hostname!r} is not a public DNS name, so Let's "
            "Encrypt is not possible — the WebUI keeps its self-signed "
            "certificate."
        )
        return None
    if not ui.confirm(
        f"Get a free Let's Encrypt certificate for {hostname}? "
        "(port 80 must be reachable from the internet)",
        default=True,
    ):
        return None
    return hostname


def setup_letsencrypt(project_dir: Path, state: AppState, hostname: str) -> None:
    """Three-phase issuance; assumes the rendered project is ready to start.

    1. Enable the certbot service + ACME plumbing (nginx still self-signed).
    2. One-off ``certonly`` through the webroot nginx now serves.
    3. Verify the cert landed on disk, then switch nginx over and restart.
    """
    state.letsencrypt_enabled = True
    state_mod.save_state(state, project_dir)
    render_project(state, project_dir)
    # Create the webroot before docker does, so it is owned by this user and
    # the challenge self-test below can write its probe file.
    (project_dir / "certbot-www").mkdir(parents=True, exist_ok=True)
    compose = docker_cli.Compose(project_dir)
    compose.up()

    ui.console.print("Testing the HTTP-01 challenge path…")
    problem = letsencrypt.probe_http_challenge(project_dir, hostname)
    if problem is not None:
        ui.error_panel(
            f"Self-test of the challenge URL failed:\n{problem}\n\n"
            "Checked from this machine — some routers block connections to "
            "their own public IP ('hairpin NAT'), so Let's Encrypt might "
            "still succeed from outside.",
            title="Challenge self-test failed",
        )
        if not ui.confirm("Try the Let's Encrypt request anyway?", default=False):
            ui.info_panel(
                "Aborted — the WebUI stays on the self-signed certificate. "
                "Fix the port-80 forwarding/DNS and retry via the certificate menu."
            )
            return

    ui.console.print("Requesting the certificate from Let's Encrypt…")
    result = compose.run_service(
        "certbot", *letsencrypt.certonly_args(hostname), entrypoint="certbot"
    )
    if result.stdout:
        ui.console.print(result.stdout.strip())

    # Never point nginx at cert files that don't exist — it would refuse to
    # start and take the box path down with it.
    if not letsencrypt.cert_exists(project_dir, hostname):
        raise CertError(
            f"certbot finished but no certificate appeared in "
            f"{letsencrypt.live_cert_dir(project_dir, hostname)}. The WebUI "
            "stays on the self-signed certificate. Check the output above "
            "and `docker compose logs certbot`, make sure port 80 is "
            "reachable from the internet, then retry via the certificate menu."
        )

    state.webui_tls_mode = "letsencrypt"
    state_mod.save_state(state, project_dir)
    render_project(state, project_dir)
    compose.up()
    # nginx.conf is bind-mounted; the compose definition did not change in
    # this phase, so `up` alone would keep nginx on the self-signed cert.
    compose.restart()
    ui.info_panel(
        f"The WebUI now serves the Let's Encrypt certificate for {hostname}.\n"
        "Renewal runs automatically twice a day (certbot side-container); "
        "nginx reloads every 6 hours to pick up renewed certs.\n\n"
        "Note: Let's Encrypt no longer sends expiry emails — this tool "
        "checks the certificate on every start and warns when fewer than "
        f"{letsencrypt.RENEWAL_WARN_DAYS} days remain.",
        title="Let's Encrypt active",
    )


def render_project(state: AppState, project_dir: Path) -> list[Path]:
    """Render all config files for the chosen mode (existing files get a .bak)."""
    if state.deployment_mode == "nginx" and not state.webui_hostname:
        raise ValueError("nginx mode needs a WebUI hostname — run the setup wizard.")
    for name in DATA_DIRS:
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    context = {
        "deployment_mode": state.deployment_mode,
        "teddycloud_image_tag": state.teddycloud_image_tag,
        "webui_port_mode": state.webui_port_mode,
        "webui_hostname": state.webui_hostname,
        "webui_port": state.webui_port,
        "http_port": state.http_port,
        "webui_client_cert_auth": state.webui_client_cert_auth,
        "basic_auth_enabled": state.basic_auth_enabled,
        "ip_allowlist": state.ip_allowlist,
        "webui_tls_mode": state.webui_tls_mode,
        "letsencrypt_enabled": state.letsencrypt_enabled,
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
    step_image_tag(state)
    le: str | None = None
    if state.deployment_mode == "nginx":
        step_webui(state)
        step_webui_auth(state, project_dir)
        step_first_client_cert(state, project_dir)
        step_protection(state, project_dir)
        if state.webui_tls_mode == "letsencrypt" and letsencrypt.cert_exists(
            project_dir, state.webui_hostname
        ):
            # Certificate already on disk — never re-request, never re-ask.
            ui.info_panel(
                f"Let's Encrypt certificate for {state.webui_hostname} is "
                "already present — keeping it."
            )
        else:
            le = step_letsencrypt(state)

    state_mod.save_state(state, project_dir)
    state_mod.save_last_project(project_dir)
    rendered = render_project(state, project_dir)
    ui.info_panel(
        "Configuration written:\n" + "\n".join(str(p) for p in rendered),
        title="Setup complete",
    )

    if not ui.confirm("Start (or restart) the services now?", default=True):
        if le is not None:
            ui.info_panel(
                "Let's Encrypt needs running services — set it up later via "
                "the certificate menu."
            )
        return
    if not project_menu.confirm_required_ports(project_dir):
        return
    if le is not None:
        setup_letsencrypt(project_dir, state, le)
    else:
        compose = docker_cli.Compose(project_dir)
        was_running = _any_running(compose)
        compose.up()
        if was_running:
            # Config files are bind-mounted: when the compose definition is
            # unchanged, `up` leaves running containers alone and nginx keeps
            # serving the OLD config — force a restart.
            compose.restart()
    ui.warn_panel(
        "Services started.\n\n"
        "On the FIRST start TeddyCloud generates its CA and server "
        "certificates — RSA key generation can take several minutes on slow "
        "hardware. Until it finishes, the WebUI answers '502 Bad Gateway'; "
        "watch progress with the 'Show recent logs' action in the Docker "
        "menu.\n\n"
        "Afterwards, export certs/server/ca.der via the certificate menu to "
        "flash it onto the box.",
        title="Services started",
    )


def _any_running(compose: docker_cli.Compose) -> bool:
    try:
        return any(svc.state == "running" for svc in compose.ps())
    except docker_cli.DockerError:
        return False
