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

Reading goes through TeddyCloud's own settings API, queried inside the
container (``compose exec``) like the doctor's /api/getBoxes, so neither
nginx routing nor WebUI auth get in the way. Writing does NOT: in
production (2026-11) ``POST /api/settings/set`` answered with an empty body
and the value never reached config.ini, so a restart turned the lock back
on. Instead TeddyCloud is stopped, ``config.ini`` is edited in a one-off
container (works whatever the bind mount's host ownership) and TeddyCloud is
started again — it reads config.ini on start, and being stopped it cannot
overwrite the edit with its in-memory value.
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

# TeddyCloud's settings file inside the container (bind mount ./config).
CONFIG_INI = "/teddycloud/config/config.ini"

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


def _wait_for_setting(
    compose: docker_cli.Compose,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    timeout: float,
) -> str:
    """Poll until TeddyCloud answers ``true``/``false`` for the setting.

    Anything else (connection refused, the lock message, an empty body or a
    404 while TeddyCloud is still starting) means "not ready yet". The
    deadline is wall-clock, so slow curl calls count against it too.
    """
    deadline = clock() + timeout
    last = ""
    while True:
        try:
            last = get_crawler_setting(compose)
            if last in ("true", "false"):
                return last
        except docker_cli.DockerError as exc:
            # Keep the compose/curl detail, not the echoed command line.
            last = str(exc).split(":\n", 1)[-1]  # still starting
        if clock() >= deadline:
            raise SecMitError(
                f"TeddyCloud did not report {CRAWLER_SETTING} within "
                f"{int(timeout)}s after the restart (last answer: {last[:120]!r}) "
                "— check `docker compose logs teddycloud`."
            )
        sleep(_READY_INTERVAL)


def _edit_config_script() -> str:
    """Shell script that sets onCrawler=false in config.ini (or appends it).

    Matches the key with optional surrounding whitespace (every such line is
    replaced, duplicates included). Rewrites the file in place via ``cat >``
    instead of ``sed -i`` so it keeps its inode, owner and mode, and adds a
    missing final newline before appending so no line gets glued on. The
    original is kept as ``config.ini.bak`` in case the rewrite is cut short.
    """
    key = CRAWLER_SETTING.replace(".", r"\.")
    line_re = f"^[[:space:]]*{key}[[:space:]]*="
    return (
        f"f={CONFIG_INI}; "
        'test -f "$f" || { echo "$f not found - start TeddyCloud once first" >&2; exit 3; }; '
        'cp -p "$f" "$f.bak" || exit 4; '
        f"if grep -q '{line_re}' \"$f\"; then "
        f"sed 's/{line_re}.*/{CRAWLER_SETTING}=false/' \"$f\" > /tmp/config.ini "
        '&& cat /tmp/config.ini > "$f"; '
        'else { [ -s "$f" ] && [ -n "$(tail -c1 "$f")" ] && echo; '
        f"echo '{CRAWLER_SETTING}=false'; }} >> \"$f\"; fi"
    )


def disable_crawler_lock(
    compose: docker_cli.Compose,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = _READY_TIMEOUT,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Turn ``security_mit.onCrawler`` off persistently in config.ini.

    Stops TeddyCloud, edits config.ini, starts the project again and
    verifies the running value — which also clears an active lock. The
    boxes are offline for those few seconds. Returns False (nothing
    touched) when the setting is already off.
    """
    answer = get_crawler_setting(compose)
    if answer == "false":
        return False
    if answer != "true" and not is_locked(answer):
        raise SecMitError(f"Unexpected answer for {CRAWLER_SETTING}: {answer[:120]!r}")
    # `compose stop teddycloud` also stops its dependents (nginx): remember
    # what was running and start exactly that again — and do it whatever
    # happens in between (Ctrl-C included), or the boxes stay cut off.
    running = [s.service for s in compose.ps() if s.state == "running"]
    compose.stop("teddycloud")
    edit_error: BaseException | None = None
    try:
        compose.run_service(
            "teddycloud", "-c", _edit_config_script(), entrypoint="sh", no_deps=True
        )
    except BaseException as exc:
        edit_error = exc
        raise
    finally:
        try:
            compose.start(*running)
        except docker_cli.DockerError as start_exc:
            cause = (
                f"Editing config.ini failed: {edit_error}\n\n"
                if edit_error is not None
                else "config.ini was edited, but "
            )
            raise SecMitError(
                f"{cause}starting the services again failed: {start_exc}\n"
                "TeddyCloud and nginx are stopped — the boxes are offline. "
                "Run `docker compose up -d`."
            ) from (edit_error or start_exc)
    answer = _wait_for_setting(compose, sleep, clock, timeout)
    if answer != "false":
        raise SecMitError(
            f"{CRAWLER_SETTING} is still {answer!r} after editing "
            f"config.ini — check {CONFIG_INI} inside the container."
        )
    return True
