from pathlib import Path

import pytest

from teddycloudhelper import state, ui
from teddycloudhelper.menus import docker as docker_menu


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

    assert docker_menu.adopt_project() == project.resolve()

    assert state.has_state(project)
    assert state.load_state(project) == state.AppState()
    assert state.load_last_project() == project.resolve()


def test_adopt_without_compose_file(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    answer_path(monkeypatch, empty)

    assert docker_menu.adopt_project() is None
    assert not state.has_state(empty)
    assert state.load_last_project() is None


def test_adopt_declined_registration(project, monkeypatch):
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, False)

    assert docker_menu.adopt_project() is None
    assert not state.has_state(project)
    assert state.load_last_project() is None


def test_adopt_existing_state_needs_no_confirm(project, monkeypatch):
    existing = state.AppState(deployment_mode="nginx")
    state.save_state(existing, project)
    answer_path(monkeypatch, project)

    def fail_confirm(*a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("confirm() must not be called when state exists")

    monkeypatch.setattr(ui, "confirm", fail_confirm)

    assert docker_menu.adopt_project() == project.resolve()
    assert state.load_state(project) == existing  # existing state untouched


def test_active_project_reuses_last(project):
    state.save_last_project(project)
    assert docker_menu._active_project() == project.resolve()


def test_active_project_falls_back_to_adopt(tmp_path, project, monkeypatch):
    stale = tmp_path / "stale"
    stale.mkdir()  # exists, but has no compose file
    state.save_last_project(stale)
    answer_path(monkeypatch, project)
    answer_confirm(monkeypatch, True)

    assert docker_menu._active_project() == project.resolve()
