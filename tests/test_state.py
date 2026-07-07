import json

import pytest

from teddycloudhelper import state


def test_save_then_load_roundtrip(tmp_path):
    original = state.AppState(
        deployment_mode="nginx",
        webui_port_mode="shared",
        webui_hostname="tc.example.com",
        ip_allowlist=["192.168.0.0/24"],
        next_serial=7,
    )
    state.save_state(original, tmp_path)
    assert state.has_state(tmp_path)
    assert state.load_state(tmp_path) == original


def test_defaults():
    s = state.AppState()
    assert s.schema_version == state.SCHEMA_VERSION
    assert s.deployment_mode == "direct"
    assert s.webui_port_mode == "separate"
    assert s.ip_allowlist == []


def test_save_creates_project_dir(tmp_path):
    project = tmp_path / "new" / "project"
    path = state.save_state(state.AppState(), project)
    assert path.is_file()
    assert json.loads(path.read_text())["schema_version"] == state.SCHEMA_VERSION


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(state.StateError, match="No teddycloudhelper.json"):
        state.load_state(tmp_path)


def test_load_invalid_json_raises(tmp_path):
    state.state_path(tmp_path).write_text("{not json")
    with pytest.raises(state.StateError, match="not valid JSON"):
        state.load_state(tmp_path)


def test_load_non_object_raises(tmp_path):
    state.state_path(tmp_path).write_text("[1, 2]")
    with pytest.raises(state.StateError, match="JSON object"):
        state.load_state(tmp_path)


def test_unknown_keys_are_ignored(tmp_path):
    data = state.AppState().to_dict() | {"some_future_field": True}
    state.state_path(tmp_path).write_text(json.dumps(data))
    assert state.load_state(tmp_path) == state.AppState()


def test_newer_schema_is_rejected(tmp_path):
    data = state.AppState().to_dict() | {"schema_version": state.SCHEMA_VERSION + 1}
    state.state_path(tmp_path).write_text(json.dumps(data))
    with pytest.raises(state.StateError, match="Upgrade teddycloudhelper"):
        state.load_state(tmp_path)


def test_migration_runs_and_writes_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SCHEMA_VERSION", state.SCHEMA_VERSION + 1)
    monkeypatch.setitem(
        state.MIGRATIONS,
        state.SCHEMA_VERSION - 1,
        lambda data: data | {"deployment_mode": "nginx"},
    )
    old = state.AppState(schema_version=state.SCHEMA_VERSION - 1).to_dict()
    state.state_path(tmp_path).write_text(json.dumps(old))

    loaded = state.load_state(tmp_path)

    assert loaded.schema_version == state.SCHEMA_VERSION
    assert loaded.deployment_mode == "nginx"
    backups = list(tmp_path.glob("teddycloudhelper.json.*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == old


def test_missing_migration_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SCHEMA_VERSION", state.SCHEMA_VERSION + 1)
    old = state.AppState(schema_version=state.SCHEMA_VERSION - 1).to_dict()
    state.state_path(tmp_path).write_text(json.dumps(old))
    with pytest.raises(state.StateError, match="No migration registered"):
        state.load_state(tmp_path)


def test_migration_v1_drops_email_for_flag(tmp_path):
    v1 = state.AppState().to_dict()
    del v1["letsencrypt_enabled"]
    v1 |= {"schema_version": 1, "letsencrypt_email": "a@b.de"}
    state.state_path(tmp_path).write_text(json.dumps(v1))

    loaded = state.load_state(tmp_path)

    assert loaded.schema_version == 2
    assert loaded.letsencrypt_enabled is True
    assert not hasattr(loaded, "letsencrypt_email")


def test_migration_v1_without_email(tmp_path):
    v1 = state.AppState().to_dict()
    del v1["letsencrypt_enabled"]
    v1 |= {"schema_version": 1, "letsencrypt_email": ""}
    state.state_path(tmp_path).write_text(json.dumps(v1))

    assert state.load_state(tmp_path).letsencrypt_enabled is False


def test_last_project_pointer_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_global_config_path", lambda: tmp_path / "cfg" / "config.json")
    project = tmp_path / "project"
    project.mkdir()

    assert state.load_last_project() is None
    state.save_last_project(project)
    assert state.load_last_project() == project.resolve()


def test_last_project_pointer_to_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_global_config_path", lambda: tmp_path / "cfg" / "config.json")
    project = tmp_path / "gone"
    project.mkdir()
    state.save_last_project(project)
    project.rmdir()
    assert state.load_last_project() is None
