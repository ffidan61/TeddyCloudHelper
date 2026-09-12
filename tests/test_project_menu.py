from pathlib import Path

import pytest

from teddycloudhelper import state, ui
from teddycloudhelper.menus import project as project_menu


@pytest.fixture(autouse=True)
def isolated_global_config(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_global_config_path", lambda: tmp_path / "cfg" / "config.json")


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / "docker-compose.yml").write_text("services: {}\n")
    return directory


def answer_path(monkeypatch, path: Path):
    monkeypatch.setattr(ui, "ask_path", lambda *a, **kw: path)


def answer_confirm(monkeypatch, value: bool):
    monkeypatch.setattr(ui, "confirm", lambda *a, **kw: value)


def test_adopt_registers_new_project(project, monkeypatch):
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, True)

    assert project_menu.adopt_project() == project.resolve()

    assert state.has_state(project)
    assert state.load_state(project) == state.AppState()
    assert state.load_last_project() == project.resolve()


def test_adopt_without_compose_file(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    answer_path(monkeypatch, empty)

    assert project_menu.adopt_project() is None
    assert not state.has_state(empty)
    assert state.load_last_project() is None


def test_adopt_declined_registration(project, monkeypatch):
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, False)

    assert project_menu.adopt_project() is None
    assert not state.has_state(project)
    assert state.load_last_project() is None


def test_adopt_existing_state_needs_no_confirm(project, monkeypatch):
    existing = state.AppState(deployment_mode="nginx")
    state.save_state(existing, project)
    answer_path(monkeypatch, project)

    def fail_confirm(*a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("confirm() must not be called when state exists")

    monkeypatch.setattr(ui, "confirm", fail_confirm)

    assert project_menu.adopt_project() == project.resolve()
    assert state.load_state(project) == existing  # existing state untouched


def test_active_project_reuses_last(project):
    state.save_last_project(project)
    assert project_menu.active_project() == project.resolve()


def test_active_project_falls_back_to_adopt(tmp_path, project, monkeypatch):
    stale = tmp_path / "stale"
    stale.mkdir()  # exists, but has no compose file
    state.save_last_project(stale)
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, True)

    assert project_menu.active_project() == project.resolve()


def answer_menu(monkeypatch, value):
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: value)


def _make_project(tmp_path, name):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "docker-compose.yml").write_text("services: {}\n")
    return directory


def test_switch_project_selects_recent(tmp_path, monkeypatch):
    tc2 = _make_project(tmp_path, "tc2")
    tc3 = _make_project(tmp_path, "tc3")
    state.save_last_project(tc2)
    state.save_last_project(tc3)  # tc3 is now the last used one
    answer_menu(monkeypatch, str(tc2.resolve()))

    assert project_menu.switch_project() == tc2.resolve()
    assert state.load_last_project() == tc2.resolve()  # pointer moved to tc2


def test_switch_project_with_no_history_adopts(project, monkeypatch):
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, True)

    assert project_menu.switch_project() == project.resolve()


def test_switch_project_other_directory_adopts(tmp_path, monkeypatch):
    existing = _make_project(tmp_path, "existing")
    state.save_last_project(existing)
    fresh = _make_project(tmp_path, "fresh")
    # Pick the "Other directory…" sentinel, then adopt `fresh`.
    monkeypatch.setattr(ui, "menu", lambda *a, **kw: "\0other")
    answer_path(monkeypatch, fresh)
    answer_confirm(monkeypatch, True)

    assert project_menu.switch_project() == fresh.resolve()
    assert state.load_last_project() == fresh.resolve()


def test_switch_project_recent_lost_compose_file(tmp_path, monkeypatch):
    good = _make_project(tmp_path, "good")
    broken = _make_project(tmp_path, "broken")
    state.save_last_project(good)
    state.save_last_project(broken)
    (broken / "docker-compose.yml").unlink()  # compose file vanished
    answer_menu(monkeypatch, str(broken.resolve()))

    assert project_menu.switch_project() is None
    assert state.load_last_project() == broken.resolve()  # pointer unchanged
