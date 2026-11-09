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


PS_RUNNING = completed(
    stdout='{"Name":"teddycloud","Service":"teddycloud","State":"running"}\n'
    '{"Name":"nginx","Service":"nginx","State":"running"}\n'
    '{"Name":"certbot","Service":"certbot","State":"exited"}\n'
)

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
        PS_RUNNING,
        completed(),  # stop
        completed(),  # run (edit)
        completed(),  # start
        completed(stdout="false"),  # verify
    )
    assert secmit.disable_crawler_lock(compose, sleep=lambda s: None) is True
    cmds = compose_cmds(runner)
    assert cmds[1] == ["ps", "--all", "--format", "json"]
    assert cmds[2] == ["stop", "teddycloud"]
    run = cmds[3]
    assert run[:6] == ["run", "--rm", "--no-deps", "--entrypoint", "sh", "teddycloud"]
    assert run[6] == "-c"
    script = run[7]
    assert secmit.CONFIG_INI in script
    assert "security_mit.onCrawler=false" in script
    assert "sed -i" not in script  # keep inode/owner/mode of config.ini
    assert 'cp -p "$f" "$f.bak"' in script
    # `stop teddycloud` also stops nginx (depends_on) — start what ran before,
    # but not the service that was stopped on purpose.
    assert cmds[4] == ["start", "teddycloud", "nginx"]
    # No settings/set or triggerWriteConfig — that path did not persist in prod.
    assert curl_urls(runner) == [
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
        "http://localhost:80/api/settings/get/security_mit.onCrawler",
    ]


def test_disable_is_noop_when_already_off(tmp_path):
    compose, runner = make_compose(tmp_path, completed(stdout="false\n"))
    assert secmit.disable_crawler_lock(compose) is False
    assert len(runner.calls) == 1


class FakeClock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_disable_handles_a_locked_instance(tmp_path):
    # The lock blocks the API, but stop/edit/start clears it anyway.
    compose, runner = make_compose(
        tmp_path,
        completed(stdout=LOCKED),
        PS_RUNNING,
        completed(),  # stop
        completed(),  # run
        completed(),  # start
        completed(returncode=7, stderr="connection refused"),  # still starting
        completed(stdout="false"),
    )
    clock = FakeClock()
    assert secmit.disable_crawler_lock(compose, sleep=clock.sleep, clock=clock) is True
    assert compose_cmds(runner)[2] == ["stop", "teddycloud"]
    assert len(clock.sleeps) == 1


@pytest.mark.parametrize("not_ready", ["", "File not found", LOCKED])
def test_disable_keeps_polling_until_a_real_answer(tmp_path, not_ready):
    compose, _ = make_compose(
        tmp_path,
        completed(stdout="true"), PS_RUNNING, completed(), completed(), completed(),
        completed(stdout=not_ready),
        completed(stdout="false"),
    )
    clock = FakeClock()
    assert secmit.disable_crawler_lock(compose, sleep=clock.sleep, clock=clock) is True
    assert len(clock.sleeps) == 1


def test_disable_starts_services_again_when_edit_fails(tmp_path):
    compose, runner = make_compose(
        tmp_path,
        completed(stdout="true"),
        PS_RUNNING,
        completed(),  # stop
        completed(returncode=3, stderr="config.ini not found"),  # run
        completed(),  # start
    )
    with pytest.raises(docker_cli.DockerError, match="not found"):
        secmit.disable_crawler_lock(compose)
    assert compose_cmds(runner)[-1] == ["start", "teddycloud", "nginx"]


def test_disable_keeps_edit_error_when_start_fails_too(tmp_path):
    compose, _ = make_compose(
        tmp_path,
        completed(stdout="true"),
        PS_RUNNING,
        completed(),  # stop
        completed(returncode=3, stderr="config.ini not found"),  # run
        completed(returncode=1, stderr="port is already allocated"),  # start
    )
    with pytest.raises(secmit.SecMitError) as info:
        secmit.disable_crawler_lock(compose)
    message = str(info.value)
    assert "config.ini not found" in message
    assert "port is already allocated" in message
    assert "docker compose up -d" in message


def test_disable_starts_services_again_on_ctrl_c(tmp_path):
    calls = []

    def runner(args, cwd):
        calls.append(args[2:])
        if args[2] == "run":
            raise KeyboardInterrupt
        if args[2] == "ps":
            return PS_RUNNING
        return completed(stdout="true")

    compose = docker_cli.Compose(tmp_path, runner=runner)
    with pytest.raises(KeyboardInterrupt):
        secmit.disable_crawler_lock(compose)
    assert calls[-1] == ["start", "teddycloud", "nginx"]


def test_disable_says_boxes_are_offline_when_start_fails(tmp_path):
    compose, _ = make_compose(
        tmp_path,
        completed(stdout="true"),
        PS_RUNNING,
        completed(),  # stop
        completed(),  # run (edit ok)
        completed(returncode=1, stderr="port is already allocated"),  # start
    )
    with pytest.raises(secmit.SecMitError) as info:
        secmit.disable_crawler_lock(compose)
    message = str(info.value)
    assert "config.ini was edited" in message
    assert "boxes are offline" in message
    assert "docker compose up -d" in message


def test_timeout_message_shows_the_cause_not_the_command(tmp_path):
    clock = FakeClock()
    compose, _ = make_compose(
        tmp_path, *[completed(returncode=1, stderr="service is not running")] * 5
    )
    with pytest.raises(secmit.SecMitError) as info:
        secmit._wait_for_setting(compose, clock.sleep, clock, 6)
    assert "service is not running" in str(info.value)


def test_disable_fails_when_value_did_not_change(tmp_path):
    compose, _ = make_compose(
        tmp_path,
        completed(stdout="true"), PS_RUNNING, completed(), completed(), completed(),
        completed(stdout="true"),
    )
    with pytest.raises(secmit.SecMitError, match="still"):
        secmit.disable_crawler_lock(compose, sleep=lambda s: None)


def test_disable_gives_up_after_the_timeout(tmp_path):
    clock = FakeClock()
    compose, _ = make_compose(
        tmp_path, completed(stdout=LOCKED), PS_RUNNING, completed(), completed(), completed(),
        *[completed(stdout=LOCKED)] * 10,
    )
    with pytest.raises(secmit.SecMitError, match="within 6s"):
        secmit.disable_crawler_lock(compose, sleep=clock.sleep, clock=clock, timeout=6)
    assert clock.now == 6


def test_timeout_counts_slow_answers(tmp_path):
    # Each curl takes 10s of wall-clock time: the 6s deadline is hit after
    # the first attempt instead of after two sleep intervals.
    clock = FakeClock()

    def slow_runner(args, cwd):
        clock.now += 10
        return completed(stdout="")

    compose = docker_cli.Compose(tmp_path, runner=slow_runner)
    with pytest.raises(secmit.SecMitError, match="within 6s"):
        secmit._wait_for_setting(compose, clock.sleep, clock, 6)
    assert clock.sleeps == []


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
