"""TeddyCloud's security mitigation ("scanner lock") and how to defuse it.

TeddyCloud locks itself — WebUI *and* box API, until the next restart — as
soon as one request carries a crawler/scanner User-Agent (Censys, Shodan,
Nmap, Nuclei, ...). That check runs before any authentication, on every
listener, including the box port 443. Port 443 has to stay open to the
internet (the box connects there, without SNI, and TeddyCloud only asks for
its client certificate *optionally*), so any internet scanner hitting
``https://<ip>/`` locks the instance and cuts the boxes off — verified
2026-10 against TeddyCloud develop (``server.c`` / ``handler_security_mit.c``)
and production logs ("Crawler access detected").

nginx cannot filter this: the box path is TLS passthrough, nginx never sees
the User-Agent. With the WebUI protected by nginx (client certs, Basic Auth,
allowlist), the crawler check protects nothing and only serves as a remote
off switch — so we turn off ``security_mit.onCrawler``. The other triggers
(robots.txt, blacklisted domains) can only arrive through the WebUI ports,
i.e. through nginx's protection; they stay on and still lock on a real
exposure.

All access goes through TeddyCloud's own settings API, queried inside the
container (``compose exec``) like the doctor's /api/getBoxes, so neither
nginx routing nor WebUI auth get in the way.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from teddycloudhelper import docker_cli
from teddycloudhelper.state import AppState

CRAWLER_SETTING = "security_mit.onCrawler"

# Body of every TeddyCloud answer while the lock is active — the settings API
# included, so a locked instance has to be restarted before it can be fixed.
LOCK_MARKER = "locked to mitigate security risks"

_API = "http://localhost:80/api"

# A restarted TeddyCloud needs a few seconds before its API answers.
_READY_TIMEOUT = 90.0
_READY_INTERVAL = 3.0


class SecMitError(Exception):
    """TeddyCloud's settings API did not behave as expected."""


def webui_protected(state: AppState) -> bool:
    """nginx guards the WebUI — only then is the crawler check redundant.
    Without it (direct mode, or nginx with no protection) the lock is the
    last line of defence for an exposed WebUI and must stay on."""
    return state.deployment_mode == "nginx" and bool(
        state.basic_auth_enabled or state.webui_client_cert_auth or state.ip_allowlist
    )


def _curl(compose: docker_cli.Compose, *args: str) -> str:
    return compose.exec_service(
        "teddycloud", "curl", "-s", "--max-time", "10", *args
    ).stdout.strip()


def get_crawler_setting(compose: docker_cli.Compose) -> str:
    """Raw answer for ``security_mit.onCrawler``: ``"true"``/``"false"``,
    or the lock message while TeddyCloud is locked.

    Raises DockerError when the container is not running or the API does
    not answer at all.
    """
    return _curl(compose, f"{_API}/settings/get/{CRAWLER_SETTING}")


def is_locked(answer: str) -> bool:
    return LOCK_MARKER in answer


def _wait_until_unlocked(
    compose: docker_cli.Compose,
    sleep: Callable[[float], None],
    timeout: float,
) -> str:
    waited = 0.0
    while True:
        try:
            answer = get_crawler_setting(compose)
            if not is_locked(answer):
                return answer
        except docker_cli.DockerError:
            pass  # still starting
        if waited >= timeout:
            raise SecMitError(
                f"TeddyCloud did not answer unlocked within {int(timeout)}s "
                "after the restart — check `docker compose logs teddycloud`."
            )
        sleep(_READY_INTERVAL)
        waited += _READY_INTERVAL


def disable_crawler_lock(
    compose: docker_cli.Compose,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = _READY_TIMEOUT,
) -> bool:
    """Turn ``security_mit.onCrawler`` off and persist it to config.ini.

    A locked instance is restarted first (the lock blocks the settings API
    too). Returns False when the setting was already off — though a lock
    may still have been cleared by the restart.
    """
    answer = get_crawler_setting(compose)
    if is_locked(answer):
        compose.restart("teddycloud")
        answer = _wait_until_unlocked(compose, sleep, timeout)
    if answer == "false":
        return False
    if answer != "true":
        raise SecMitError(f"Unexpected answer for {CRAWLER_SETTING}: {answer[:120]!r}")
    result = _curl(
        compose, "-X", "POST", "--data-raw", "false",
        f"{_API}/settings/set/{CRAWLER_SETTING}",
    )
    if result != "OK":
        raise SecMitError(f"Setting {CRAWLER_SETTING} failed: {result[:120]!r}")
    # /api/settings/set only changes the running instance; this writes
    # config.ini so the setting survives restarts and image updates.
    _curl(compose, f"{_API}/triggerWriteConfig")
    if get_crawler_setting(compose) != "false":
        raise SecMitError(f"{CRAWLER_SETTING} is still on after setting it.")
    return True
