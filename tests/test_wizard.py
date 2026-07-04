import subprocess

import pytest

from teddycloudhelper import docker_cli, ui, wizard
from teddycloudhelper import state as state_mod
from teddycloudhelper.certs import ca, crl, server_certs
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


def test_step_webui_separate_port(monkeypatch):
    state = AppState()
    answer_text(monkeypatch, "tc.home.arpa", "9443")

    menus = iter(["separate"])
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: next(menus))
    wizard.step_webui(state)

    assert state.webui_hostname == "tc.home.arpa"
    assert state.webui_port_mode == "separate"
    assert state.webui_port == 9443


def test_step_webui_shared_asks_no_port(monkeypatch):
    state = AppState(webui_port=8443)
    answer_text(monkeypatch, "tc.home.arpa")  # only the hostname prompt exists
    answer_menu(monkeypatch, "shared")

    wizard.step_webui(state)

    assert state.webui_port_mode == "shared"
    assert state.webui_port == 8443  # untouched


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


def test_step_letsencrypt_defaults_to_yes_for_public_hostname(monkeypatch):
    state = AppState(webui_hostname="tc.example.com")
    recorded = {}

    def confirm(message, default=None):
        recorded["default"] = default
        return default

    monkeypatch.setattr(ui, "confirm", confirm)
    answer_text(monkeypatch, "a@b.de")

    assert wizard.step_letsencrypt(state) == ("tc.example.com", "a@b.de")
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


def test_step_letsencrypt_reprompts_on_bad_email(monkeypatch):
    state = AppState(webui_hostname="tc.example.com")
    answer_confirm(monkeypatch, True)
    answer_text(monkeypatch, "not-an-email", "a@b.de")
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)

    assert wizard.step_letsencrypt(state) == ("tc.example.com", "a@b.de")


class FakeCompose:
    """Records lifecycle calls; stands in for docker_cli.Compose."""

    calls: list[tuple] = []

    def __init__(self, project_dir):
        self.project_dir = project_dir

    def up(self):
        FakeCompose.calls.append(("up",))

    def run_service(self, service, *args, entrypoint=None):
        FakeCompose.calls.append(("run", service, entrypoint, *args))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def test_setup_letsencrypt_three_phases(tmp_path, monkeypatch):
    FakeCompose.calls = []
    monkeypatch.setattr(docker_cli, "Compose", FakeCompose)
    quiet_panels(monkeypatch)
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")

    wizard.setup_letsencrypt(tmp_path, state, "tc.example.com", "a@b.de")

    # phase 1: up with certbot plumbing, then certonly, then up again
    assert FakeCompose.calls[0] == ("up",)
    assert FakeCompose.calls[1][:2] == ("run", "certbot")
    assert FakeCompose.calls[1][2] == "certbot"  # entrypoint override, or it hangs
    assert "certonly" in FakeCompose.calls[1]
    assert "tc.example.com" in FakeCompose.calls[1]
    assert FakeCompose.calls[2] == ("up",)
    # state persisted with the final TLS mode
    saved = state_mod.load_state(tmp_path)
    assert saved.webui_tls_mode == "letsencrypt"
    assert saved.letsencrypt_email == "a@b.de"
    # nginx config now points at the LE cert
    assert "letsencrypt/live/tc.example.com" in (tmp_path / "nginx" / "nginx.conf").read_text()


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


def test_render_project_rerender_backs_up(tmp_path):
    wizard.render_project(AppState(), tmp_path)
    wizard.render_project(AppState(deployment_mode="nginx", webui_hostname="x"), tmp_path)
    assert len(list(tmp_path.glob("docker-compose.yml.*.bak"))) == 1


def test_render_project_nginx_requires_hostname(tmp_path):
    state = AppState(deployment_mode="nginx", webui_hostname="")
    with pytest.raises(ValueError, match="WebUI hostname"):
        wizard.render_project(state, tmp_path)
    assert not (tmp_path / "docker-compose.yml").exists()  # nothing half-written
