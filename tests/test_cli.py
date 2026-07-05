"""Headless --doctor mode: cron-friendly exit codes, no prompts."""

from teddycloudhelper import cli, doctor
from teddycloudhelper import state as state_mod
from teddycloudhelper.doctor import CheckResult
from teddycloudhelper.state import AppState


def make_project(tmp_path):
    state_mod.save_state(AppState(), tmp_path)
    return tmp_path


def run_doctor(monkeypatch, tmp_path, results):
    monkeypatch.setattr(doctor, "run_checks", lambda *a, **kw: results)
    return cli.main(["--doctor", "--project", str(tmp_path)])


def test_doctor_exit_zero_when_healthy(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    assert run_doctor(monkeypatch, project, [CheckResult("X", "ok", "fine")]) == 0


def test_doctor_warnings_do_not_fail_the_run(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    results = [CheckResult("X", "ok", "fine"), CheckResult("Y", "warn", "meh")]
    assert run_doctor(monkeypatch, project, results) == 0


def test_doctor_exit_one_on_failure(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    results = [CheckResult("X", "fail", "broken")]
    assert run_doctor(monkeypatch, project, results) == 1


def test_doctor_exit_two_without_project(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "load_last_project", lambda: None)
    assert cli.main(["--doctor"]) == 2


def test_doctor_exit_two_without_state(tmp_path):
    # Directory exists but is not a TeddyCloudHelper project.
    assert cli.main(["--doctor", "--project", str(tmp_path)]) == 2


def test_doctor_records_ca_fingerprint(tmp_path, monkeypatch):
    # The headless run must persist what check_ca_identity recorded.
    project = make_project(tmp_path)

    def fake_checks(project_dir, state, probes=None):
        state.known_ca_fingerprint = "abc123"
        return [CheckResult("Server CA", "ok", "recorded")]

    monkeypatch.setattr(doctor, "run_checks", fake_checks)
    cli.main(["--doctor", "--project", str(project)])

    assert state_mod.load_state(project).known_ca_fingerprint == "abc123"
