"""Opt-in integration tests against a real Docker daemon.

Enable with ``TCH_DOCKER_TESTS=1``. They validate the rendered configs
with the real tools — ``nginx -t`` for nginx.conf and ``docker compose
config`` for the compose file — catching template regressions that string
assertions cannot see (bad directives, missing semicolons, cert paths).
No long-running containers are started, only one-off ``docker run``s.
"""

import os
import shutil
import subprocess
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
        "-v", f"{project / 'webui-pki'}:/etc/teddycloudhelper/webui-pki:ro",
    ]
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
