import subprocess

import pytest

from teddycloudhelper import docker_cli, ui, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import ca, client_certs, crl, server_certs
from teddycloudhelper.state import AppState


def answer_menu(monkeypatch, value):
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: value)


def answer_text(monkeypatch, *values):
    answers = iter(values)
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: next(answers))


def answer_confirm(monkeypatch, value):
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: value)


def quiet_panels(monkeypatch):
    monkeypatch.setattr(ui, "info_panel", lambda *a, **kw: None)


def test_step_deployment_mode(monkeypatch):
    state = AppState()
    answer_menu(monkeypatch, "nginx")
    wizard.step_deployment_mode(state)
    assert state.deployment_mode == "nginx"


def test_step_image_tag(monkeypatch):
    state = AppState()
    answer_menu(monkeypatch, "develop")
    wizard.step_image_tag(state)
    assert state.teddycloud_image_tag == "develop"


def test_step_webui_separate_port(monkeypatch):
    state = AppState()
    answer_text(monkeypatch, "tc.home.arpa", "9443")

    menus = iter(["separate"])
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: next(menus))
    wizard.step_webui(state)

    assert state.webui_hostname == "tc.home.arpa"
    assert state.webui_port_mode == "separate"
    assert state.webui_port == 9443


def test_step_webui_shared_asks_no_port_but_warns(monkeypatch):
    state = AppState(webui_port=8443)
    answer_text(monkeypatch, "tc.home.arpa")  # only the hostname prompt exists
    answer_menu(monkeypatch, "shared")
    warnings = []
    monkeypatch.setattr(ui, "warn_panel", lambda msg, **kw: warnings.append(msg))

    wizard.step_webui(state)

    assert state.webui_port_mode == "shared"
    assert state.webui_port == 8443  # untouched
    # Shared 443 only works when the box has its own hostname — the wizard
    # must say so (SNI collision cost a full debugging day in prod).
    assert warnings and "own DNS name" in warnings[0]


def test_ask_port_rejects_garbage_until_valid(monkeypatch):
    answer_text(monkeypatch, "abc", "70000", "0", "9443")
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)
    assert wizard._ask_port(8443) == 9443


def test_step_webui_auth_recreates_cert_after_hostname_change(tmp_path, monkeypatch):
    server_certs.create_webui_server_cert(tmp_path, "old.example.com")
    state = AppState(webui_hostname="new.example.com")
    answer_confirm(monkeypatch, False)
    quiet_panels(monkeypatch)

    wizard.step_webui_auth(state, tmp_path)

    assert server_certs.webui_cert_matches(tmp_path, "new.example.com")


def test_step_webui_auth_creates_server_cert_and_ca(tmp_path, monkeypatch):
    state = AppState(webui_hostname="tc.home.arpa")
    answer_confirm(monkeypatch, True)
    quiet_panels(monkeypatch)

    wizard.step_webui_auth(state, tmp_path)

    assert state.webui_client_cert_auth is True
    assert (server_certs.server_dir(tmp_path) / "server.crt").is_file()
    assert ca.ca_exists(tmp_path)
    assert crl.crl_path(tmp_path).is_file()


def test_step_webui_auth_does_not_reask_when_enabled(tmp_path, monkeypatch):
    # Reconfiguration must not nag about an option that is already on —
    # the security menu owns the toggle.
    state = AppState(webui_hostname="tc.home.arpa", webui_client_cert_auth=True)
    quiet_panels(monkeypatch)

    def boom(*a, **kw):
        raise AssertionError("must not prompt")

    monkeypatch.setattr(ui, "confirm", boom)

    wizard.step_webui_auth(state, tmp_path)

    assert state.webui_client_cert_auth is True
    assert ca.ca_exists(tmp_path)  # CA still ensured


def test_step_webui_auth_declined_creates_no_ca(tmp_path, monkeypatch):
    state = AppState(webui_hostname="tc.home.arpa")
    answer_confirm(monkeypatch, False)
    quiet_panels(monkeypatch)

    wizard.step_webui_auth(state, tmp_path)

    assert state.webui_client_cert_auth is False
    assert not ca.ca_exists(tmp_path)
    # the server cert is always needed for the TLS-terminated WebUI
    assert (server_certs.server_dir(tmp_path) / "server.crt").is_file()


def test_step_webui_auth_keeps_existing_material(tmp_path, monkeypatch):
    state = AppState(webui_hostname="tc.home.arpa", webui_client_cert_auth=True)
    server_certs.create_webui_server_cert(tmp_path, "tc.home.arpa")
    ca.create_ca(tmp_path)
    crl.ensure_crl(tmp_path)
    crl.revoke_serial(tmp_path, 7)
    before = ca.ca_cert_path(tmp_path).read_bytes()
    answer_confirm(monkeypatch, True)
    quiet_panels(monkeypatch)

    wizard.step_webui_auth(state, tmp_path)

    assert ca.ca_cert_path(tmp_path).read_bytes() == before
    assert crl.revoked_serials(tmp_path) == [7]  # CRL not reset


def test_step_first_client_cert_issues_and_persists(tmp_path, monkeypatch):
    ca.create_ca(tmp_path)
    state = AppState(webui_client_cert_auth=True, next_serial=5)
    answer_confirm(monkeypatch, True)
    answer_text(monkeypatch, "admin")
    monkeypatch.setattr(ui, "ask_password", lambda *a, **kw: "pw")
    quiet_panels(monkeypatch)

    wizard.step_first_client_cert(state, tmp_path)

    infos = client_certs.list_client_certs(tmp_path)
    assert [(i.name, i.serial) for i in infos] == [("admin", 5)]
    assert state.next_serial == 6
    # serial counter persisted even though run() saves later
    from teddycloudhelper import state as state_mod

    assert state_mod.load_state(tmp_path).next_serial == 6


def test_step_first_client_cert_skips_without_auth(tmp_path, monkeypatch):
    state = AppState(webui_client_cert_auth=False)
    monkeypatch.setattr(
        ui, "confirm", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    wizard.step_first_client_cert(state, tmp_path)  # must not prompt


def test_step_first_client_cert_skips_when_certs_exist(tmp_path, monkeypatch):
    ca.create_ca(tmp_path)
    client_certs.issue_client_cert(tmp_path, "existing", serial=1, p12_password="pw")
    state = AppState(webui_client_cert_auth=True)
    monkeypatch.setattr(
        ui, "confirm", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    wizard.step_first_client_cert(state, tmp_path)


def test_step_first_client_cert_declined(tmp_path, monkeypatch):
    ca.create_ca(tmp_path)
    state = AppState(webui_client_cert_auth=True)
    answer_confirm(monkeypatch, False)
    quiet_panels(monkeypatch)

    wizard.step_first_client_cert(state, tmp_path)

    assert client_certs.list_client_certs(tmp_path) == []


def test_step_letsencrypt_defaults_to_yes_for_public_hostname(monkeypatch):
    state = AppState(webui_hostname="tc.example.com")
    recorded = {}

    def confirm(message, default=None):
        recorded["default"] = default
        return default

    monkeypatch.setattr(ui, "confirm", confirm)
    # no email prompt anymore — ask_text must not be called
    monkeypatch.setattr(
        ui, "ask_text", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no prompt"))
    )

    assert wizard.step_letsencrypt(state) == "tc.example.com"
    assert recorded["default"] is True  # LE is the default in nginx mode


def test_step_letsencrypt_skips_non_public_hostname(monkeypatch):
    state = AppState(webui_hostname="teddycloud.local")
    quiet_panels(monkeypatch)
    monkeypatch.setattr(
        ui, "confirm", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    assert wizard.step_letsencrypt(state) is None


def test_step_letsencrypt_declined(monkeypatch):
    state = AppState(webui_hostname="tc.example.com")
    answer_confirm(monkeypatch, False)
    assert wizard.step_letsencrypt(state) is None


class FakeCompose:
    """Records lifecycle calls; stands in for docker_cli.Compose."""

    calls: list[tuple] = []
    issue_cert = True  # plant the cert file when certbot "runs"

    def __init__(self, project_dir):
        self.project_dir = project_dir

    def up(self):
        FakeCompose.calls.append(("up",))

    def restart(self):
        FakeCompose.calls.append(("restart",))

    def run_service(self, service, *args, entrypoint=None):
        FakeCompose.calls.append(("run", service, entrypoint, *args))
        if service == "certbot" and FakeCompose.issue_cert:
            from teddycloudhelper.certs import letsencrypt

            live = letsencrypt.live_cert_dir(self.project_dir, "tc.example.com")
            live.mkdir(parents=True, exist_ok=True)
            (live / "fullchain.pem").write_text("pem")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def quiet_probe(monkeypatch, result=None):
    from teddycloudhelper.certs import letsencrypt

    monkeypatch.setattr(letsencrypt, "probe_http_challenge", lambda *a, **kw: result)


def test_setup_letsencrypt_three_phases(tmp_path, monkeypatch):
    FakeCompose.calls = []
    FakeCompose.issue_cert = True
    monkeypatch.setattr(docker_cli, "Compose", FakeCompose)
    quiet_panels(monkeypatch)
    quiet_probe(monkeypatch)
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")

    wizard.setup_letsencrypt(tmp_path, state, "tc.example.com")

    # phase 1: up with certbot plumbing, then certonly, then up again
    assert FakeCompose.calls[0] == ("up",)
    assert FakeCompose.calls[1][:2] == ("run", "certbot")
    assert FakeCompose.calls[1][2] == "certbot"  # entrypoint override, or it hangs
    assert "certonly" in FakeCompose.calls[1]
    assert "tc.example.com" in FakeCompose.calls[1]
    # phase 3 must force a restart: nginx.conf is bind-mounted, `up` alone
    # would leave nginx on the self-signed cert
    assert FakeCompose.calls[2:] == [("up",), ("restart",)]
    # state persisted with the final TLS mode
    saved = state_mod.load_state(tmp_path)
    assert saved.webui_tls_mode == "letsencrypt"
    assert saved.letsencrypt_enabled is True
    # nginx config now points at the LE cert
    assert "letsencrypt/live/tc.example.com" in (tmp_path / "nginx" / "nginx.conf").read_text()


def test_setup_letsencrypt_aborts_when_no_cert_appears(tmp_path, monkeypatch):
    FakeCompose.calls = []
    FakeCompose.issue_cert = False  # certbot "succeeds" but writes nothing
    monkeypatch.setattr(docker_cli, "Compose", FakeCompose)
    quiet_panels(monkeypatch)
    quiet_probe(monkeypatch)
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")

    with pytest.raises(wizard.CertError, match="no certificate appeared"):
        wizard.setup_letsencrypt(tmp_path, state, "tc.example.com")

    # nginx must never be switched to nonexistent cert paths
    saved = state_mod.load_state(tmp_path)
    assert saved.webui_tls_mode == "selfsigned"
    assert "letsencrypt/live" not in (tmp_path / "nginx" / "nginx.conf").read_text()
    assert ("restart",) not in FakeCompose.calls


def test_setup_letsencrypt_aborts_when_probe_fails_and_user_declines(tmp_path, monkeypatch):
    FakeCompose.calls = []
    monkeypatch.setattr(docker_cli, "Compose", FakeCompose)
    quiet_panels(monkeypatch)
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)
    quiet_probe(monkeypatch, result="port 80 was not reachable")
    answer_confirm(monkeypatch, False)  # do not try anyway
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")

    wizard.setup_letsencrypt(tmp_path, state, "tc.example.com")

    # certbot was never invoked, nginx stays self-signed
    assert all(call[0] != "run" for call in FakeCompose.calls)
    assert state_mod.load_state(tmp_path).webui_tls_mode == "selfsigned"


def test_step_protection_offers_basic_auth(tmp_path, monkeypatch):
    # An unprotected WebUI trips TeddyCloud's security-mitigation lock —
    # the wizard must not end without at least offering protection.
    from teddycloudhelper import security

    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")
    answer_confirm(monkeypatch, True)
    answer_text(monkeypatch, "admin")
    monkeypatch.setattr(ui, "ask_password", lambda *a, **kw: "secret")
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)
    quiet_panels(monkeypatch)

    wizard.step_protection(state, tmp_path)

    assert state.basic_auth_enabled is True
    assert security.load_users(tmp_path) == ["admin"]


def test_step_protection_declined_changes_nothing(tmp_path, monkeypatch):
    from teddycloudhelper import security

    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")
    answer_confirm(monkeypatch, False)
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)
    quiet_panels(monkeypatch)

    wizard.step_protection(state, tmp_path)

    assert state.basic_auth_enabled is False
    assert security.load_users(tmp_path) == []


@pytest.mark.parametrize(
    "state",
    [
        AppState(basic_auth_enabled=True),
        AppState(webui_client_cert_auth=True),
        AppState(ip_allowlist=["192.168.0.0/24"]),
    ],
)
def test_step_protection_skips_when_already_protected(tmp_path, monkeypatch, state):
    def boom(*a, **kw):
        raise AssertionError("must not prompt")

    monkeypatch.setattr(ui, "confirm", boom)
    monkeypatch.setattr(ui, "error_panel", boom)

    wizard.step_protection(state, tmp_path)  # no prompts, no changes


def test_render_project_direct(tmp_path):
    rendered = wizard.render_project(AppState(), tmp_path)
    assert rendered == [tmp_path / "docker-compose.yml"]
    assert not (tmp_path / "nginx").exists()


def test_render_project_nginx(tmp_path):
    state = AppState(
        deployment_mode="nginx", webui_hostname="tc.home.arpa", webui_port_mode="shared"
    )
    rendered = wizard.render_project(state, tmp_path)

    assert rendered == [
        tmp_path / "docker-compose.yml",
        tmp_path / "nginx" / "nginx.conf",
    ]
    conf = (tmp_path / "nginx" / "nginx.conf").read_text()
    assert "tc.home.arpa" in conf


def test_render_project_creates_data_dirs(tmp_path):
    # Bind-mount sources must exist user-owned before docker creates them
    # as root; includes the upstream data dirs (cache, plugins, …).
    wizard.render_project(AppState(), tmp_path)
    for name in wizard.DATA_DIRS:
        assert (tmp_path / name).is_dir()


def test_render_project_rerender_backs_up(tmp_path):
    wizard.render_project(AppState(), tmp_path)
    wizard.render_project(AppState(deployment_mode="nginx", webui_hostname="x"), tmp_path)
    assert len(list(tmp_path.glob("docker-compose.yml.*.bak"))) == 1


def test_render_project_nginx_requires_hostname(tmp_path):
    state = AppState(deployment_mode="nginx", webui_hostname="")
    with pytest.raises(ValueError, match="WebUI hostname"):
        wizard.render_project(state, tmp_path)
    assert not (tmp_path / "docker-compose.yml").exists()  # nothing half-written


# --- check_nginx_before_restart -----------------------------------------------


def _write_nginx_conf(tmp_path, text="events {}\n"):
    (tmp_path / "nginx").mkdir(exist_ok=True)
    conf = tmp_path / "nginx" / "nginx.conf"
    conf.write_text(text)
    return conf


def test_check_nginx_noop_in_direct_mode(tmp_path, monkeypatch):
    # No nginx.conf -> the validator must not even run.
    def boom(*a, **kw):
        raise AssertionError("must not validate")

    monkeypatch.setattr(docker_cli, "nginx_config_test", boom)
    wizard.check_nginx_before_restart(tmp_path)  # no raise


def test_check_nginx_ok_config_passes(tmp_path, monkeypatch):
    _write_nginx_conf(tmp_path)
    monkeypatch.setattr(
        docker_cli, "nginx_config_test", lambda p: subprocess.CompletedProcess([], 0)
    )
    wizard.check_nginx_before_restart(tmp_path)  # no raise


def test_check_nginx_config_error_rolls_back_and_raises(tmp_path, monkeypatch):
    conf = _write_nginx_conf(tmp_path, "BROKEN new config\n")
    # A previous good version render_to_file would have left as a .bak.
    backup = tmp_path / "nginx" / "nginx.conf.20260706-120000.bak"
    backup.write_text("events {}\n# good\n")
    monkeypatch.setattr(
        docker_cli,
        "nginx_config_test",
        lambda p: subprocess.CompletedProcess(
            [], 1, stderr="nginx: [emerg] unknown directive\nnginx: configuration test failed"
        ),
    )

    with pytest.raises(docker_cli.DockerError, match="failed `nginx -t`"):
        wizard.check_nginx_before_restart(tmp_path)

    # rolled back to the last good config
    assert conf.read_text() == "events {}\n# good\n"


def test_check_nginx_config_error_without_backup_still_raises(tmp_path, monkeypatch):
    _write_nginx_conf(tmp_path, "BROKEN\n")
    monkeypatch.setattr(
        docker_cli,
        "nginx_config_test",
        lambda p: subprocess.CompletedProcess([], 1, stderr="nginx: configuration test failed"),
    )
    with pytest.raises(docker_cli.DockerError):
        wizard.check_nginx_before_restart(tmp_path)


def test_check_nginx_unavailable_docker_is_noop(tmp_path, monkeypatch):
    _write_nginx_conf(tmp_path)
    monkeypatch.setattr(docker_cli, "nginx_config_test", lambda p: None)
    wizard.check_nginx_before_restart(tmp_path)  # no raise, restart proceeds


def test_check_nginx_infra_error_warns_but_proceeds(tmp_path, monkeypatch):
    # docker run failed for a non-config reason -> don't block the restart.
    _write_nginx_conf(tmp_path)
    warnings = []
    monkeypatch.setattr(ui, "warn_panel", lambda msg, **kw: warnings.append(msg))
    monkeypatch.setattr(
        docker_cli,
        "nginx_config_test",
        lambda p: subprocess.CompletedProcess(
            [], 125, stderr="Cannot connect to the Docker daemon"
        ),
    )

    wizard.check_nginx_before_restart(tmp_path)  # no raise

    assert warnings and "Could not validate" in warnings[0]
