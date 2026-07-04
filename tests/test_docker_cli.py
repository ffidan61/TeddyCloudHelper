import json
import subprocess
from pathlib import Path

import pytest

from teddycloudhelper import docker_cli


def completed(args=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=args or [], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FakeRunner:
    """Records compose invocations and replays queued results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, args, cwd):
        self.calls.append((args, cwd))
        return self.results.pop(0) if self.results else completed(args)


def make_compose(tmp_path, *results):
    runner = FakeRunner(*results)
    return docker_cli.Compose(tmp_path, runner=runner), runner


PS_ENTRY = {
    "Name": "teddycloud",
    "Service": "teddycloud",
    "State": "running",
    "Status": "Up 2 hours (healthy)",
    "Health": "healthy",
}


def test_ps_parses_ndjson(tmp_path):
    stdout = json.dumps(PS_ENTRY) + "\n" + json.dumps(PS_ENTRY | {"Name": "nginx"}) + "\n"
    compose, runner = make_compose(tmp_path, completed(stdout=stdout))

    services = compose.ps()

    assert runner.calls == [(["docker", "compose", "ps", "--all", "--format", "json"], tmp_path)]
    assert [s.name for s in services] == ["teddycloud", "nginx"]
    assert services[0] == docker_cli.ServiceStatus(
        name="teddycloud",
        service="teddycloud",
        state="running",
        status="Up 2 hours (healthy)",
        health="healthy",
    )


def test_ps_parses_json_array(tmp_path):
    compose, _ = make_compose(tmp_path, completed(stdout=json.dumps([PS_ENTRY])))
    assert [s.state for s in compose.ps()] == ["running"]


def test_ps_empty_output(tmp_path):
    compose, _ = make_compose(tmp_path, completed(stdout="\n"))
    assert compose.ps() == []


def test_ps_missing_fields_default_empty(tmp_path):
    compose, _ = make_compose(tmp_path, completed(stdout="{}"))
    assert compose.ps() == [docker_cli.ServiceStatus("", "", "", "", "")]


def test_ps_garbage_output_raises(tmp_path):
    compose, _ = make_compose(tmp_path, completed(stdout="not json"))
    with pytest.raises(docker_cli.DockerError, match="Could not parse"):
        compose.ps()


def test_failure_raises_with_stderr(tmp_path):
    compose, _ = make_compose(tmp_path, completed(returncode=1, stderr="no such service"))
    with pytest.raises(docker_cli.DockerError, match="exit code 1") as excinfo:
        compose.stop()
    assert "no such service" in str(excinfo.value)


def test_failure_without_output(tmp_path):
    compose, _ = make_compose(tmp_path, completed(returncode=125))
    with pytest.raises(docker_cli.DockerError, match="exit code 125"):
        compose.up()


@pytest.mark.parametrize(
    ("method", "expected_args"),
    [
        ("up", ["up", "--detach", "--remove-orphans"]),
        ("stop", ["stop"]),
        ("restart", ["restart"]),
        ("pull", ["pull"]),
    ],
)
def test_lifecycle_commands_build_args(tmp_path, method, expected_args):
    compose, runner = make_compose(tmp_path)
    getattr(compose, method)()
    assert runner.calls == [(["docker", "compose", *expected_args], tmp_path)]


def test_logs_returns_stdout(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="line1\nline2\n"))
    assert compose.logs(tail=50) == "line1\nline2\n"
    assert runner.calls[0][0] == ["docker", "compose", "logs", "--no-color", "--tail", "50"]


def test_find_compose_file_precedence(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    assert docker_cli.find_compose_file(tmp_path) == tmp_path / "compose.yaml"


def test_find_compose_file_none(tmp_path):
    assert docker_cli.find_compose_file(tmp_path) is None


def test_default_runner_missing_binary(tmp_path):
    with pytest.raises(docker_cli.DockerError, match="Could not run"):
        docker_cli._default_runner(["definitely-not-a-real-binary-xyz"], tmp_path)
