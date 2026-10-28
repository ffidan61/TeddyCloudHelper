import subprocess

import pytest

from teddycloudhelper import docker_cli, secmit
from teddycloudhelper.state import AppState


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FakeRunner:
    """Records compose invocations and replays queued results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, cwd):
        self.calls.append((args, cwd))
        return self.results.pop(0) if self.results else completed()


def make_compose(tmp_path, *results):
    runner = FakeRunner(*results)
    return docker_cli.Compose(tmp_path, runner=runner), runner


LOCKED = "TeddyCloud has been locked to mitigate security risks! Please check the logs"


def curl_urls(runner) -> list[str]:
    return [args[-1] for args, _ in runner.calls if "curl" in args]


def test_disable_sets_persists_and_verifies(tmp_path):
    compose, runner = make_compose(
        tmp_path,
        completed(stdout="true"),
        completed(stdout="OK"),
        completed(stdout=""),
        completed(stdout="false"),
    )
    assert secmit.disable_crawler_lock(compose) is True
    set_call = runner.calls[1][0]
    assert set_call[set_call.index("--data-raw") + 1] == "false"
    assert curl_urls(runner) == [
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
        "http://localhost:80/api/settings/set/security_mit.onCrawler",
        "http://localhost:80/api/triggerWriteConfig",
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
    ]


def test_disable_is_noop_when_already_off(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="false\n"))
    assert secmit.disable_crawler_lock(compose) is False
    assert len(runner.calls) == 1


def test_disable_restarts_a_locked_instance_first(tmp_path):
    # The lock blocks the settings API too — only a restart clears it.
    compose, runner = make_compose(
        tmp_path,
        completed(stdout=LOCKED),
        completed(),  # compose restart teddycloud
        completed(returncode=7, stderr="connection refused"),  # still starting
        completed(stdout="true"),
        completed(stdout="OK"),
        completed(),
        completed(stdout="false"),
    )
    sleeps = []
    assert secmit.disable_crawler_lock(compose, sleep=sleeps.append) is True
    assert runner.calls[1][0] == ["docker", "compose", "restart", "teddycloud"]
    assert len(sleeps) == 1


def test_disable_gives_up_when_still_locked(tmp_path):
    compose, _ = make_compose(
        tmp_path, completed(stdout=LOCKED), completed(),
        *[completed(stdout=LOCKED)] * 10,
    )
    with pytest.raises(secmit.SecMitError, match="did not answer unlocked"):
        secmit.disable_crawler_lock(compose, sleep=lambda s: None, timeout=6)


def test_disable_rejects_failed_set(tmp_path):
    compose, _ = make_compose(
        tmp_path, completed(stdout="true"), completed(stdout="ERROR")
    )
    with pytest.raises(secmit.SecMitError, match="failed"):
        secmit.disable_crawler_lock(compose)


def test_disable_rejects_unexpected_answer(tmp_path):
    compose, _ = make_compose(tmp_path, completed(stdout="<html>login</html>"))
    with pytest.raises(secmit.SecMitError, match="Unexpected"):
        secmit.disable_crawler_lock(compose)


def test_container_down_raises_docker_error(tmp_path):
    compose, _ = make_compose(tmp_path, completed(returncode=1, stderr="not running"))
    with pytest.raises(docker_cli.DockerError):
        secmit.disable_crawler_lock(compose)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (AppState(deployment_mode="nginx", webui_client_cert_auth=True), True),
        (AppState(deployment_mode="nginx", basic_auth_enabled=True), True),
        (AppState(deployment_mode="nginx", ip_allowlist=["10.0.0.0/8"]), True),
        (AppState(deployment_mode="nginx"), False),
        # direct mode: nginx enforces nothing, whatever the state says
        (AppState(deployment_mode="direct", webui_client_cert_auth=True), False),
    ],
)
def test_webui_protected(state, expected):
    assert secmit.webui_protected(state) is expected
