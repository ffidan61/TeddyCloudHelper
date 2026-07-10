"""Project-settings menu: single-option changes without the full wizard."""

import pytest

from teddycloudhelper import ui
from teddycloudhelper.certs import server_certs
from teddycloudhelper.menus import settings
from teddycloudhelper.state import AppState


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(ui, "info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "error_panel", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "warn_panel", lambda *a, **kw: None)
    monkeypatch.setattr(ui.console, "print", lambda *a, **kw: None)


def no_restart(monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: False)


def test_hostname_change_regenerates_selfsigned_cert(tmp_path, monkeypatch, quiet):
    state = AppState(deployment_mode="nginx", webui_hostname="old.example.com")
    server_certs.create_webui_server_cert(tmp_path, "old.example.com")
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "new.example.com")
    no_restart(monkeypatch)

    settings._hostname(state, tmp_path)

    assert state.webui_hostname == "new.example.com"
    assert server_certs.webui_cert_matches(tmp_path, "new.example.com")
    assert (tmp_path / "docker-compose.yml").is_file()  # re-rendered


def test_hostname_change_resets_letsencrypt_without_cert(tmp_path, monkeypatch, quiet):
    state = AppState(
        deployment_mode="nginx",
        webui_hostname="old.example.com",
        webui_tls_mode="letsencrypt",
    )
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "new.example.com")
    no_restart(monkeypatch)

    settings._hostname(state, tmp_path)

    # No cert on disk for the new name -> nginx must not point at it.
    assert state.webui_tls_mode == "selfsigned"


def test_hostname_change_keeps_letsencrypt_when_cert_exists(tmp_path, monkeypatch, quiet):
    # Never re-request a certificate that is already on disk — the files
    # decide, not a prompt.
    state = AppState(
        deployment_mode="nginx",
        webui_hostname="old.example.com",
        webui_tls_mode="letsencrypt",
    )
    live = tmp_path / "letsencrypt" / "live" / "new.example.com"
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_bytes(b"cert")
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "new.example.com")
    no_restart(monkeypatch)

    settings._hostname(state, tmp_path)

    assert state.webui_tls_mode == "letsencrypt"


def test_hostname_unchanged_is_a_noop(tmp_path, monkeypatch, quiet):
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "tc.example.com")

    settings._hostname(state, tmp_path)

    assert not (tmp_path / "docker-compose.yml").exists()  # nothing rendered


def test_mode_switch_to_nginx_requires_wizard(tmp_path, monkeypatch, quiet):
    state = AppState(deployment_mode="direct")

    settings._mode(state, tmp_path)  # only shows an error panel

    assert state.deployment_mode == "direct"
    assert not (tmp_path / "docker-compose.yml").exists()


def test_mode_switch_to_direct(tmp_path, monkeypatch, quiet):
    state = AppState(deployment_mode="nginx", webui_hostname="tc.example.com")
    answers = iter([True, False])  # confirm switch, decline restart
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: next(answers))

    settings._mode(state, tmp_path)

    assert state.deployment_mode == "direct"
    assert (tmp_path / "docker-compose.yml").is_file()


def test_image_channel_switch_renders_new_tag(tmp_path, monkeypatch, quiet):
    # Moved here from the Docker menu in v0.16.1 — it is a config change
    # (state + re-render), so it belongs with the other project settings.
    state = AppState(deployment_mode="direct", teddycloud_image_tag="latest")
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: "develop")
    no_restart(monkeypatch)  # decline "pull and restart"

    settings._image_channel(state, tmp_path)

    assert state.teddycloud_image_tag == "develop"
    assert ":develop" in (tmp_path / "docker-compose.yml").read_text()


def test_image_channel_same_tag_is_a_noop(tmp_path, monkeypatch, quiet):
    state = AppState(teddycloud_image_tag="latest")
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: "latest")

    settings._image_channel(state, tmp_path)

    assert not (tmp_path / "docker-compose.yml").exists()


def test_rerender_asks_before_replacing_a_foreign_compose(tmp_path, monkeypatch, quiet):
    # The doctor's mount check points adopted installs (hand-written compose)
    # at this action — it must not silently replace their file.
    state = AppState(deployment_mode="direct")
    (tmp_path / "docker-compose.yml").write_text("# my hand-written compose")
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: False)  # decline

    settings._rerender(state, tmp_path)

    assert (tmp_path / "docker-compose.yml").read_text() == "# my hand-written compose"
    assert not list(tmp_path.glob("*.bak"))


def test_rerender_regenerates_configs_without_changing_state(
    tmp_path, monkeypatch, quiet
):
    # The escape hatch after a TeddyCloudHelper update: stale rendered files
    # (e.g. missing a newly added mount) get regenerated as-is.
    state = AppState(deployment_mode="direct")
    (tmp_path / "docker-compose.yml").write_text(
        "# Generated by TeddyCloudHelper — stale, pre-update render\nservices: {}\n"
    )
    before = state.__dict__.copy()
    no_restart(monkeypatch)

    settings._rerender(state, tmp_path)

    assert state.__dict__ == before
    text = (tmp_path / "docker-compose.yml").read_text()
    assert "library/custom_img" in text  # current template, not the stale file
    assert list(tmp_path.glob("docker-compose.yml.*.bak"))  # old file backed up


def test_port_mode_switch_to_separate_asks_port(tmp_path, monkeypatch, quiet):
    state = AppState(
        deployment_mode="nginx",
        webui_hostname="tc.example.com",
        webui_port_mode="shared",
    )
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: "separate")
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "9443")
    no_restart(monkeypatch)

    settings._port_mode(state, tmp_path)

    assert state.webui_port_mode == "separate"
    assert state.webui_port == 9443
    assert (tmp_path / "docker-compose.yml").is_file()
