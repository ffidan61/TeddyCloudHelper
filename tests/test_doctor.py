"""Health checks with fake probes — no Docker, no sockets (CLAUDE.md)."""

import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from teddycloudhelper import docker_cli, doctor
from teddycloudhelper.docker_cli import ServiceStatus
from teddycloudhelper.state import AppState


def make_probes(**overrides) -> doctor.Probes:
    """Probes where everything is healthy; override per test."""
    defaults = dict(
        ps=lambda: [ServiceStatus("teddycloud", "teddycloud", "running", "Up")],
        listening=lambda port: True,
        tls_cert=lambda port, sni: make_cert("TeddyCloud", "TeddyCloud CA"),
        http_get=lambda port, sni, host: 200,
        resolve=lambda host: ["192.168.1.10"],
    )
    return doctor.Probes(**(defaults | overrides))


def make_cert(subject_cn: str, issuer_cn: str, issuer_org: str | None = None):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    issuer_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]
    if issuer_org:
        issuer_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, issuer_org))
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(x509.Name(issuer_attrs))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=90))
        .sign(key, hashes.SHA256())
    )


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


# --- containers ---------------------------------------------------------------


def test_containers_all_running():
    result = doctor.check_containers(make_probes())
    assert result.status == "ok"


def test_containers_stopped_service_fails():
    probes = make_probes(
        ps=lambda: [
            ServiceStatus("teddycloud", "teddycloud", "running", "Up"),
            ServiceStatus("teddycloud-nginx", "nginx", "exited", "Exited (1)"),
        ]
    )
    result = doctor.check_containers(probes)
    assert result.status == "fail"
    assert "nginx (exited)" in result.detail


def test_containers_none_is_warning():
    result = doctor.check_containers(make_probes(ps=list))
    assert result.status == "warn"


def test_containers_docker_error_fails():
    probes = make_probes(ps=_raise(docker_cli.DockerError("daemon down")))
    result = doctor.check_containers(probes)
    assert result.status == "fail"
    assert "daemon down" in result.detail


# --- ports ---------------------------------------------------------------------


def test_ports_all_listening():
    assert doctor.check_ports(AppState(), make_probes()).status == "ok"


def test_ports_silent_port_fails():
    probes = make_probes(listening=lambda port: port != 443)
    result = doctor.check_ports(AppState(), probes)
    assert result.status == "fail"
    assert "443" in result.detail


# --- box TLS (the SNI-routing check) --------------------------------------------


def test_box_tls_teddycloud_cert_ok():
    result = doctor.check_box_tls(AppState(), make_probes())
    assert result.status == "ok"
    assert "TeddyCloud" in result.detail


def test_box_tls_probes_without_sni():
    seen = []

    def tls_cert(port, sni):
        seen.append((port, sni))
        return make_cert("TeddyCloud", "TeddyCloud CA")

    doctor.check_box_tls(AppState(), make_probes(tls_cert=tls_cert))
    assert seen == [(443, None)]  # a Toniebox sends no SNI


def test_box_tls_letsencrypt_cert_fails():
    cert = make_cert("tc.example.com", "R11", issuer_org="Let's Encrypt")
    result = doctor.check_box_tls(
        AppState(webui_hostname="tc.example.com"),
        make_probes(tls_cert=lambda port, sni: cert),
    )
    assert result.status == "fail"
    assert "Let's Encrypt" in result.detail


def test_box_tls_webui_cert_fails():
    cert = make_cert("tc.example.com", "TeddyCloudHelper WebUI CA")
    result = doctor.check_box_tls(
        AppState(webui_hostname="tc.example.com"),
        make_probes(tls_cert=lambda port, sni: cert),
    )
    assert result.status == "fail"
    assert "WebUI certificate" in result.detail


def test_box_tls_handshake_abort_is_warning():
    # TeddyCloud may abort the handshake when the probe has no box client
    # cert — that is not a routing failure.
    probes = make_probes(tls_cert=_raise(ssl.SSLError("alert handshake failure")))
    assert doctor.check_box_tls(AppState(), probes).status == "warn"


def test_box_tls_connection_refused_fails():
    probes = make_probes(tls_cert=_raise(ConnectionRefusedError("refused")))
    assert doctor.check_box_tls(AppState(), probes).status == "fail"


# --- WebUI ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected_port", "expected_sni"),
    [
        (AppState(), 8443, None),
        (AppState(deployment_mode="nginx", webui_hostname="tc.x.de"), 8443, "tc.x.de"),
        (
            AppState(
                deployment_mode="nginx",
                webui_hostname="tc.x.de",
                webui_port_mode="shared",
            ),
            443,
            "tc.x.de",
        ),
    ],
)
def test_webui_endpoint_per_mode(state, expected_port, expected_sni):
    seen = []

    def http_get(port, sni, host):
        seen.append((port, sni))
        return 200

    result = doctor.check_webui(state, make_probes(http_get=http_get))
    assert result.status == "ok"
    assert seen == [(expected_port, expected_sni)]


def test_webui_502_is_warning():
    result = doctor.check_webui(AppState(), make_probes(http_get=lambda *a: 502))
    assert result.status == "warn"
    assert "502" in result.detail


def test_webui_401_ok_with_basic_auth():
    probes = make_probes(http_get=lambda *a: 401)
    assert doctor.check_webui(AppState(basic_auth_enabled=True), probes).status == "ok"
    assert doctor.check_webui(AppState(), probes).status == "warn"


def test_webui_400_ok_with_client_cert_auth():
    # nginx answers 400 when ssl_verify_client is on and no cert was sent.
    probes = make_probes(http_get=lambda *a: 400)
    state = AppState(webui_client_cert_auth=True)
    assert doctor.check_webui(state, probes).status == "ok"
    assert doctor.check_webui(AppState(), probes).status == "warn"


def test_webui_tls_reject_ok_with_client_cert_auth():
    probes = make_probes(http_get=_raise(ssl.SSLError("certificate required")))
    state = AppState(webui_client_cert_auth=True)
    assert doctor.check_webui(state, probes).status == "ok"
    assert doctor.check_webui(AppState(), probes).status == "fail"


def test_webui_unreachable_fails():
    probes = make_probes(http_get=_raise(ConnectionRefusedError("refused")))
    assert doctor.check_webui(AppState(), probes).status == "fail"


# --- DNS ------------------------------------------------------------------------


def test_dns_private_ip_ok():
    result = doctor.check_box_dns(make_probes())
    assert result.status == "ok"
    assert "192.168.1.10" in result.detail


def test_dns_public_ip_warns():
    result = doctor.check_box_dns(make_probes(resolve=lambda host: ["104.16.1.1"]))
    assert result.status == "warn"
    assert "Boxine" in result.detail


def test_dns_unresolvable_warns():
    probes = make_probes(resolve=_raise(OSError("NXDOMAIN")))
    assert doctor.check_box_dns(probes).status == "warn"


# --- box certs / files / letsencrypt ---------------------------------------------


def test_box_certs_present(tmp_path):
    client_dir = tmp_path / "certs" / "client"
    client_dir.mkdir(parents=True)
    for name in doctor.BOX_CERT_FILES:
        (client_dir / name).write_bytes(b"x")
    assert doctor.check_box_certs(tmp_path).status == "ok"


def test_box_certs_missing_is_warning(tmp_path):
    assert doctor.check_box_certs(tmp_path).status == "warn"


def test_box_certs_partial_fails(tmp_path):
    client_dir = tmp_path / "certs" / "client"
    client_dir.mkdir(parents=True)
    (client_dir / "ca.der").write_bytes(b"x")
    result = doctor.check_box_certs(tmp_path)
    assert result.status == "fail"
    assert "private.der" in result.detail


def test_files_missing_compose_fails(tmp_path):
    assert doctor.check_files(tmp_path, AppState()).status == "fail"


def test_files_nginx_mode_needs_nginx_conf(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    state = AppState(deployment_mode="nginx", webui_hostname="x")
    assert doctor.check_files(tmp_path, state).status == "fail"
    (tmp_path / "nginx").mkdir()
    (tmp_path / "nginx" / "nginx.conf").write_text("events {}\n")
    assert doctor.check_files(tmp_path, state).status == "ok"


def test_letsencrypt_missing_cert_warns(tmp_path):
    state = AppState(
        deployment_mode="nginx",
        webui_hostname="tc.example.com",
        webui_tls_mode="letsencrypt",
    )
    result = doctor.check_letsencrypt(tmp_path, state)
    assert result.status == "warn"
    assert "no certificate" in result.detail.lower()


# --- run_checks -----------------------------------------------------------------


def test_run_checks_includes_letsencrypt_only_when_active(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    probes = make_probes()
    names = [r.name for r in doctor.run_checks(tmp_path, AppState(), probes)]
    assert "Let's Encrypt" not in names

    (tmp_path / "nginx").mkdir()
    (tmp_path / "nginx" / "nginx.conf").write_text("events {}\n")
    state = AppState(
        deployment_mode="nginx",
        webui_hostname="tc.example.com",
        webui_tls_mode="letsencrypt",
    )
    names = [r.name for r in doctor.run_checks(tmp_path, state, probes)]
    assert "Let's Encrypt" in names


def test_run_checks_healthy_project_all_ok(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    client_dir = tmp_path / "certs" / "client"
    client_dir.mkdir(parents=True)
    for name in doctor.BOX_CERT_FILES:
        (client_dir / name).write_bytes(b"x")
    results = doctor.run_checks(tmp_path, AppState(), make_probes())
    assert all(r.status == "ok" for r in results), [
        (r.name, r.detail) for r in results if r.status != "ok"
    ]
