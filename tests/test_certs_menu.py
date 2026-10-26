"""Certificate menu flows — reissue must never orphan a revocable serial."""

import pytest

from teddycloudhelper import state as state_mod
from teddycloudhelper import ui
from teddycloudhelper.certs import ca, client_certs, crl
from teddycloudhelper.menus import certs as certs_menu
from teddycloudhelper.state import AppState


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "info_panel", lambda *a, **kw: None)
    ca.create_ca(tmp_path)
    crl.ensure_crl(tmp_path)
    state_mod.save_state(AppState(), tmp_path)
    return tmp_path


def _issue_via_menu(project, monkeypatch, confirms):
    answers = iter(confirms)
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "admin")
    monkeypatch.setattr(ui, "ask_password", lambda *a, **kw: "")
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: next(answers))
    certs_menu._issue(project)


def test_reissue_revokes_the_old_serial(project, monkeypatch):
    # Reissuing overwrites the old cert's files; without auto-revocation its
    # serial would stay valid for nginx forever but be unrevokable via the
    # menu (revoke candidates are read from the files on disk).
    _issue_via_menu(project, monkeypatch, confirms=[])
    old_serial = client_certs.list_client_certs(project)[0].serial

    # Reissue: confirm the overwrite, decline the restart.
    _issue_via_menu(project, monkeypatch, confirms=[True, False])

    new_serial = client_certs.list_client_certs(project)[0].serial
    assert new_serial != old_serial
    assert old_serial in crl.revoked_serials(project)
    assert new_serial not in crl.revoked_serials(project)


def test_reissue_declined_changes_nothing(project, monkeypatch):
    _issue_via_menu(project, monkeypatch, confirms=[])
    serial = client_certs.list_client_certs(project)[0].serial

    _issue_via_menu(project, monkeypatch, confirms=[False])

    assert client_certs.list_client_certs(project)[0].serial == serial
    assert crl.revoked_serials(project) == []


class ShareStarted(Exception):
    """Raised by the fake OneTimeShare — the flow got as far as serving."""


def _share_answers(monkeypatch, confirms):
    answers = iter(confirms)
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: "admin")
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: next(answers))
    created = []

    def fake_share(*args, **kwargs):
        created.append(args)
        raise ShareStarted

    monkeypatch.setattr(certs_menu.share, "OneTimeShare", fake_share)
    return created


def test_share_unprotected_p12_declined_serves_nothing(project, monkeypatch):
    # A passwordless .p12 over plain HTTP is the private key in cleartext
    # on the network — the default answer must be "no".
    client_certs.issue_client_cert(project, "admin", serial=1, p12_password="")
    created = _share_answers(monkeypatch, confirms=[False])
    certs_menu._share_p12(project)
    assert created == []


def test_share_protected_p12_asks_no_extra_question(project, monkeypatch):
    client_certs.issue_client_cert(project, "admin", serial=1, p12_password="pw")
    created = _share_answers(monkeypatch, confirms=[])
    monkeypatch.setattr(ui, "ask_text", lambda *a, **kw: "192.168.1.5")
    monkeypatch.setattr(ui, "warn_panel", lambda *a, **kw: None)
    with pytest.raises(ShareStarted):
        certs_menu._share_p12(project)
    assert len(created) == 1
