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


def compose_cmds(runner) -> list[list[str]]:
    """Compose sub-commands (without the leading ``docker compose``)."""
    return [args[2:] for args, _ in runner.calls]


def test_disable_edits_config_ini_with_teddycloud_stopped(tmp_path):
    compose, runner = make_compose(
        tmp_path,
        completed(stdout="true"),  # get
        completed(),  # stop
        completed(),  # run (edit)
        completed(),  # start
        completed(stdout="false"),  # verify
    )
    assert secmit.disable_crawler_lock(compose, sleep=lambda s: None) is True
    cmds = compose_cmds(runner)
    assert cmds[1] == ["stop", "teddycloud"]
    run = cmds[2]
    assert run[:6] == ["run", "--rm", "--no-deps", "--entrypoint", "sh", "teddycloud"]
    assert run[6] == "-c"
    script = run[7]
    assert secmit.CONFIG_INI in script
    assert "security_mit.onCrawler=false" in script
    assert "sed -i" not in script  # keep inode/owner/mode of config.ini
    assert cmds[3] == ["start", "teddycloud"]
    # No settings/set or triggerWriteConfig — that path did not persist in prod.
    assert curl_urls(runner) == [
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
    ]


def test_disable_is_noop_when_already_off(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="false\n"))
    assert secmit.disable_crawler_lock(compose) is False
    assert len(runner.calls) == 1


def test_disable_handles_a_locked_instance(tmp_path):
    # The lock blocks the API, but stop/edit/start clears it anyway.
    compose, runner = make_compose(
        tmp_path,
        completed(stdout=LOCKED),
        completed(),  # stop
        completed(),  # run
        completed(),  # start
        completed(returncode=7, stderr="connection refused"),  # still starting
        completed(stdout="false"),
    )
    sleeps = []
    assert secmit.disable_crawler_lock(compose, sleep=sleeps.append) is True
    assert compose_cmds(runner)[1] == ["stop", "teddycloud"]
    assert len(sleeps) == 1


def test_disable_starts_teddycloud_again_when_edit_fails(tmp_path):
    compose, runner = make_compose(
        tmp_path,
        completed(stdout="true"),
        completed(),  # stop
        completed(returncode=3, stderr="config.ini not found"),  # run
        completed(),  # start
    )
    with pytest.raises(docker_cli.DockerError, match="not found"):
        secmit.disable_crawler_lock(compose)
    assert compose_cmds(runner)[-1] == ["start", "teddycloud"]


def test_disable_fails_when_value_did_not_change(tmp_path):
    compose, _ = make_compose(
        tmp_path,
        completed(stdout="true"), completed(), completed(), completed(),
        completed(stdout="true"),
    )
    with pytest.raises(secmit.SecMitError, match="still"):
        secmit.disable_crawler_lock(compose, sleep=lambda s: None)


def test_disable_gives_up_when_still_locked(tmp_path):
    compose, _ = make_compose(
        tmp_path, completed(stdout=LOCKED), completed(), completed(), completed(),
        *[completed(stdout=LOCKED)] * 10,
    )
    with pytest.raises(secmit.SecMitError, match="did not answer unlocked"):
        secmit.disable_crawler_lock(compose, sleep=lambda s: None, timeout=6)


def test_disable_rejects_unexpected_answer(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="<html>login</html>"))
    with pytest.raises(secmit.SecMitError, match="Unexpected"):
        secmit.disable_crawler_lock(compose)
    assert len(runner.calls) == 1  # nothing stopped


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
