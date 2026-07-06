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


def test_down_without_volumes(tmp_path):
    compose, runner = make_compose(tmp_path)
    compose.down()
    assert runner.calls == [(["docker", "compose", "down", "--remove-orphans"], tmp_path)]


def test_down_with_volumes(tmp_path):
    compose, runner = make_compose(tmp_path)
    compose.down(volumes=True)
    assert runner.calls == [
        (["docker", "compose", "down", "--remove-orphans", "--volumes"], tmp_path)
    ]


def test_run_service_builds_args(tmp_path):
    compose, runner = make_compose(tmp_path)
    compose.run_service("certbot", "renew", "--webroot")
    assert runner.calls == [
        (["docker", "compose", "run", "--rm", "certbot", "renew", "--webroot"], tmp_path)
    ]


def test_run_service_with_entrypoint_override(tmp_path):
    # Needed for services whose entrypoint is a long-running loop (certbot
    # renewer): without the override the loop runs and the args are ignored.
    compose, runner = make_compose(tmp_path)
    compose.run_service("certbot", "certonly", entrypoint="certbot")
    assert runner.calls == [
        (
            ["docker", "compose", "run", "--rm", "--entrypoint", "certbot",
             "certbot", "certonly"],
            tmp_path,
        )
    ]


def test_logs_returns_stdout(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="line1\nline2\n"))
    assert compose.logs(tail=50) == "line1\nline2\n"
    assert runner.calls[0][0] == ["docker", "compose", "logs", "--no-color", "--tail", "50"]


def test_logs_for_single_service(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="nginx line\n"))
    assert compose.logs(tail=50, service="nginx") == "nginx line\n"
    assert runner.calls[0][0] == [
        "docker", "compose", "logs", "--no-color", "--tail", "50", "nginx",
    ]


def test_logs_follow_uses_stream_runner(tmp_path):
    streamed = []
    compose = docker_cli.Compose(
        tmp_path, stream_runner=lambda args, cwd: streamed.append((args, cwd))
    )
    compose.logs_follow(service="teddycloud")
    assert streamed == [
        (["docker", "compose", "logs", "--follow", "--tail", "50", "teddycloud"], tmp_path)
    ]


def test_logs_follow_all_services(tmp_path):
    streamed = []
    compose = docker_cli.Compose(tmp_path, stream_runner=lambda args, cwd: streamed.append(args))
    compose.logs_follow()
    assert streamed == [["docker", "compose", "logs", "--follow", "--tail", "50"]]


def test_exec_service_builds_args(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="{}"))
    compose.exec_service("teddycloud", "curl", "-s", "http://localhost/api/getBoxes")
    assert runner.calls == [
        (
            ["docker", "compose", "exec", "-T", "teddycloud",
             "curl", "-s", "http://localhost/api/getBoxes"],
            tmp_path,
        )
    ]


def test_find_compose_file_precedence(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    assert docker_cli.find_compose_file(tmp_path) == tmp_path / "compose.yaml"


def test_find_compose_file_none(tmp_path):
    assert docker_cli.find_compose_file(tmp_path) is None


def test_default_runner_missing_binary(tmp_path):
    with pytest.raises(docker_cli.DockerError, match="Could not run"):
        docker_cli._default_runner(["definitely-not-a-real-binary-xyz"], tmp_path)


# --- nginx_config_test --------------------------------------------------------


def _nginx_project(tmp_path):
    (tmp_path / "nginx").mkdir()
    (tmp_path / "nginx" / "nginx.conf").write_text("events {}\n")
    (tmp_path / "webui-pki").mkdir()


def test_nginx_config_test_builds_docker_run_args(tmp_path):
    _nginx_project(tmp_path)
    runner = FakeRunner(completed(returncode=0, stderr="test is successful"))

    result = docker_cli.nginx_config_test(tmp_path, runner=runner)

    assert result.returncode == 0
    args, cwd = runner.calls[0]
    assert cwd == tmp_path
    assert args[:3] == ["docker", "run", "--rm"]
    assert "--add-host" in args and "teddycloud:127.0.0.1" in args
    assert f"{tmp_path / 'nginx' / 'nginx.conf'}:/etc/nginx/nginx.conf:ro" in args
    assert args[-3:] == [docker_cli.NGINX_IMAGE, "nginx", "-t"]
    # security / letsencrypt not present -> not mounted
    assert not any("/etc/teddycloudhelper/security" in a for a in args)
    assert not any("/etc/letsencrypt" in a for a in args)


def test_nginx_config_test_mounts_optional_dirs_when_present(tmp_path):
    _nginx_project(tmp_path)
    (tmp_path / "security").mkdir()
    (tmp_path / "letsencrypt").mkdir()
    runner = FakeRunner(completed(returncode=0))

    docker_cli.nginx_config_test(tmp_path, runner=runner)

    args = runner.calls[0][0]
    assert any(a.endswith("/etc/teddycloudhelper/security:ro") for a in args)
    assert any(a.endswith("/etc/letsencrypt:ro") for a in args)


def test_nginx_config_test_returns_none_when_docker_unavailable(tmp_path):
    _nginx_project(tmp_path)

    def boom(args, cwd):
        raise docker_cli.DockerError("Could not run 'docker'")

    assert docker_cli.nginx_config_test(tmp_path, runner=boom) is None
