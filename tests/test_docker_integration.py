"""Opt-in integration tests against a real Docker daemon.

Enable with ``TCH_DOCKER_TESTS=1``. They validate the rendered configs
with the real tools — ``nginx -t`` for nginx.conf and ``docker compose
config`` for the compose file — catching template regressions that string
assertions cannot see (bad directives, missing semicolons, cert paths).
The runtime tests at the end start short-lived containers on a private
network and remove them again.
"""

import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from teddycloudhelper import security, wizard
from teddycloudhelper.certs import ca, crl, server_certs
from teddycloudhelper.state import AppState

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("TCH_DOCKER_TESTS") != "1",
        reason="Docker integration tests are opt-in: set TCH_DOCKER_TESTS=1",
    ),
]

NGINX_IMAGE = "nginx:stable-alpine"


def make_nginx_project(tmp_path: Path, **state_kwargs) -> AppState:
    """Render a full nginx-mode project with all referenced files in place."""
    state = AppState(
        deployment_mode="nginx", webui_hostname="tc.example.com", **state_kwargs
    )
    server_certs.create_webui_server_cert(tmp_path, state.webui_hostname)
    if state.webui_client_cert_auth:
        ca.create_ca(tmp_path)
        crl.ensure_crl(tmp_path)
    if state.basic_auth_enabled:
        security.set_user(tmp_path, "admin", "secret")
    if state.webui_tls_mode == "letsencrypt":
        # nginx -t loads the cert files, so real PEM material is needed.
        live = tmp_path / "letsencrypt" / "live" / state.webui_hostname
        live.mkdir(parents=True)
        server = server_certs.server_dir(tmp_path)
        shutil.copy(server / "server.crt", live / "fullchain.pem")
        shutil.copy(server / "server.key", live / "privkey.pem")
    wizard.render_project(state, tmp_path)
    return state


def nginx_t(project: Path) -> subprocess.CompletedProcess:
    args = [
        "docker", "run", "--rm",
        # The config proxies to the compose service name; nginx resolves it
        # at config-check time already.
        "--add-host", "teddycloud:127.0.0.1",
        "-v", f"{project / 'nginx' / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
    ]
    pki, pki_target = project / "webui-pki", "/etc/teddycloudhelper/webui-pki"
    for rel in ("server", "ca/ca.crt", "crl"):
        if (pki / rel).exists():
            args += ["-v", f"{pki / rel}:{pki_target}/{rel}:ro"]
    if (project / "security").is_dir():
        args += ["-v", f"{project / 'security'}:/etc/teddycloudhelper/security:ro"]
    if (project / "letsencrypt").is_dir():
        args += ["-v", f"{project / 'letsencrypt'}:/etc/letsencrypt:ro"]
    args += [NGINX_IMAGE, "nginx", "-t"]
    return subprocess.run(args, capture_output=True, text=True)


def assert_nginx_ok(project: Path) -> None:
    result = nginx_t(project)
    assert result.returncode == 0, f"nginx -t failed:\n{result.stderr}"


def test_nginx_conf_separate_selfsigned(tmp_path):
    make_nginx_project(tmp_path)
    assert_nginx_ok(tmp_path)


def test_nginx_conf_shared_selfsigned(tmp_path):
    make_nginx_project(tmp_path, webui_port_mode="shared")
    assert_nginx_ok(tmp_path)


def test_nginx_conf_all_security_features(tmp_path):
    make_nginx_project(
        tmp_path,
        webui_client_cert_auth=True,
        basic_auth_enabled=True,
        ip_allowlist=["192.168.0.0/24", "10.0.0.5"],
    )
    assert_nginx_ok(tmp_path)


def test_nginx_conf_satisfy_any_bypass(tmp_path):
    make_nginx_project(
        tmp_path,
        basic_auth_enabled=True,
        ip_allowlist=["192.168.0.0/24", "10.0.0.5"],
        ip_bypasses_basic_auth=True,
    )
    assert_nginx_ok(tmp_path)


def test_nginx_conf_letsencrypt(tmp_path):
    make_nginx_project(tmp_path, webui_tls_mode="letsencrypt", letsencrypt_enabled=True)
    assert_nginx_ok(tmp_path)


@pytest.mark.parametrize(
    "state",
    [
        AppState(),  # direct mode
        AppState(deployment_mode="nginx", webui_hostname="tc.example.com"),
        AppState(
            deployment_mode="nginx",
            webui_hostname="tc.example.com",
            letsencrypt_enabled=True,
        ),
    ],
    ids=["direct", "nginx", "nginx-letsencrypt"],
)
def test_compose_file_is_valid(tmp_path, state):
    wizard.render_project(state, tmp_path)
    result = subprocess.run(
        ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml"), "config", "--quiet"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"


# --- runtime: real client IPs through nginx ---------------------------------------
#
# `nginx -t` only checks syntax — it could not catch that v0.21.1 put the
# PROXY header on a loopback hop that only ever reported 127.0.0.1 (prod,
# 2026-10-26: allowlist blocked everyone). These tests run the rendered
# nginx for real on a private Docker network, next to a fake TeddyCloud,
# and fetch from clients with fixed IPs.

CURL_IMAGE = "curlimages/curl:latest"

FAKE_TEDDYCLOUD_CONF = """
events {}
http {
    server { listen 80; location / { return 200 "webui-ok"; } }
    server {
        listen 443 ssl;
        ssl_certificate     /certs/server.crt;
        ssl_certificate_key /certs/server.key;
        location / { return 200 "box-ok"; }
    }
}
"""


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} failed:\n{result.stderr}")
    return result


@pytest.fixture
def docker_net(tmp_path):
    """A private network with a fixed subnet; everything is torn down after."""
    tag = uuid.uuid4().hex[:8]
    subnet = f"172.31.{random.randint(100, 250)}"
    net = f"tch-it-{tag}"
    _docker("network", "create", "--subnet", f"{subnet}.0/24", net)
    containers: list[str] = []
    try:
        yield {"net": net, "subnet": subnet, "tag": tag, "containers": containers}
    finally:
        for name in containers:
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", net, check=False)


def _start_stack(project: Path, env: dict) -> str:
    """Fake teddycloud + the rendered nginx; returns nginx's IP."""
    fake_conf = project / "fake-teddycloud.conf"
    fake_conf.write_text(FAKE_TEDDYCLOUD_CONF)
    certs = server_certs.server_dir(project)
    fake = f"tch-it-tc-{env['tag']}"
    env["containers"].append(fake)
    _docker(
        "run", "-d", "--name", fake, "--network", env["net"],
        "--network-alias", "teddycloud",
        "-v", f"{fake_conf}:/etc/nginx/nginx.conf:ro",
        "-v", f"{certs}:/certs:ro",
        NGINX_IMAGE,
    )
    nginx_ip = f"{env['subnet']}.10"
    proxy = f"tch-it-nginx-{env['tag']}"
    env["containers"].append(proxy)
    _docker(
        "run", "-d", "--name", proxy, "--network", env["net"], "--ip", nginx_ip,
        "-v", f"{project / 'nginx' / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
        "-v", f"{certs}:/etc/teddycloudhelper/webui-pki/server:ro",
        NGINX_IMAGE,
    )
    return nginx_ip


def _curl(env: dict, client_ip: str, *args: str) -> str:
    """Run curl from a container with a fixed source IP; returns stdout."""
    result = _docker(
        "run", "--rm", "--network", env["net"], "--ip", client_ip, CURL_IMAGE,
        "-sk", "--max-time", "5",
        # nginx / the fake backend may still be starting: retry refused
        # connections and 5xx, but never a 403 (that is the answer we test).
        "--retry", "15", "--retry-connrefused", "--retry-delay", "1",
        *args,
    )
    return result.stdout


@pytest.mark.parametrize(
    ("port_mode", "webui_url"),
    [("shared", "https://tc.example.com/"), ("separate", "https://tc.example.com:8443/")],
)
def test_runtime_allowlist_sees_real_client_ip(tmp_path, docker_net, port_mode, webui_url):
    env = docker_net
    allowed, denied = f"{env['subnet']}.20", f"{env['subnet']}.21"
    make_nginx_project(tmp_path, webui_port_mode=port_mode, ip_allowlist=[allowed])
    nginx_ip = _start_stack(tmp_path, env)
    port = "443" if port_mode == "shared" else "8443"
    status = ["--resolve", f"tc.example.com:{port}:{nginx_ip}",
              "-o", "/dev/null", "-w", "%{http_code}", webui_url]

    assert _curl(env, allowed, *status) == "200"
    assert _curl(env, denied, *status) == "403"
    # nginx logged the denied client's REAL address, not 127.0.0.1.
    logs = _docker("logs", f"tch-it-nginx-{env['tag']}").stderr
    assert f"client: {denied}" in logs
    assert "client: 127.0.0.1" not in logs


def test_runtime_shared_box_path_reaches_teddycloud_without_proxy_header(
    tmp_path, docker_net
):
    # The box sends no SNI (connect by IP) -> default branch -> box hop.
    # If the PROXY header leaked through, the fake TeddyCloud's TLS
    # handshake would fail instead of answering "box-ok".
    env = docker_net
    client = f"{env['subnet']}.20"
    make_nginx_project(tmp_path, webui_port_mode="shared", ip_allowlist=[client])
    nginx_ip = _start_stack(tmp_path, env)

    assert _curl(env, client, f"https://{nginx_ip}/") == "box-ok"
    # The allowlist guards the WebUI only — the box path ignores it.
    assert _curl(env, f"{env['subnet']}.22", f"https://{nginx_ip}/") == "box-ok"
