"""Health checks with fake probes — no Docker, no sockets (CLAUDE.md)."""

import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
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
        http_get=lambda port, sni, host, path: (200, "<html>teddycloud</html>"),
        resolve=lambda host: ["192.168.1.10"],
        getboxes=lambda: '{"boxes":[{"ID":"1","commonName":"001122334455","boxName":"Kids"}]}',
        local_image_digest=lambda image: "sha256:abc",
        remote_image_digest=lambda image: "sha256:abc",
        teddycloud_mounts=lambda: healthy_mounts(),
    )
    return doctor.Probes(**(defaults | overrides))


def healthy_mounts() -> dict[str, str]:
    """Every expected mount present; both custom_img paths share one source."""
    mounts = {dest: f"/host{dest}" for dest in doctor.TEDDYCLOUD_MOUNTS}
    mounts["/teddycloud/data/library/custom_img"] = mounts[
        "/teddycloud/data/www/custom_img"
    ]
    return mounts


def make_cert(
    subject_cn: str, issuer_cn: str, issuer_org: str | None = None, days: int = 90
):
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
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
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


def test_containers_unhealthy_fails():
    probes = make_probes(
        ps=lambda: [
            ServiceStatus("teddycloud", "teddycloud", "running", "Up (unhealthy)", "unhealthy")
        ]
    )
    result = doctor.check_containers(probes)
    assert result.status == "fail"
    assert "unhealthy" in result.detail


def test_containers_starting_warns():
    probes = make_probes(
        ps=lambda: [
            ServiceStatus("teddycloud", "teddycloud", "running", "Up (starting)", "starting")
        ]
    )
    result = doctor.check_containers(probes)
    assert result.status == "warn"
    assert "starting" in result.detail.lower()


# --- container mounts ------------------------------------------------------------


def test_mounts_all_present():
    result = doctor.check_mounts(make_probes())
    assert result.status == "ok"


def test_mounts_missing_mount_fails_with_rerender_hint():
    # The exact prod incident: compose rendered before the template gained
    # the library/custom_img mount — WebUI image uploads 500'd.
    stale = healthy_mounts()
    del stale["/teddycloud/data/library/custom_img"]
    result = doctor.check_mounts(make_probes(teddycloud_mounts=lambda: stale))
    assert result.status == "fail"
    assert "/teddycloud/data/library/custom_img" in result.detail
    assert "Re-render" in result.detail


def test_mounts_diverging_custom_img_sources_fail():
    # Both paths mounted, but from different host dirs: uploads land outside
    # the served directory and never show up in the WebUI.
    split = healthy_mounts()
    split["/teddycloud/data/library/custom_img"] = "/host/elsewhere"
    result = doctor.check_mounts(make_probes(teddycloud_mounts=lambda: split))
    assert result.status == "fail"
    assert "custom_img" in result.detail


def test_mounts_extra_mounts_are_fine():
    extra = healthy_mounts() | {"/teddycloud/data/www/extra": "/host/extra"}
    assert doctor.check_mounts(make_probes(teddycloud_mounts=lambda: extra)).status == "ok"


def test_mounts_docker_error_warns():
    probes = make_probes(
        teddycloud_mounts=_raise(docker_cli.DockerError("no teddycloud container"))
    )
    result = doctor.check_mounts(probes)
    assert result.status == "warn"
    assert "no teddycloud container" in result.detail


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

    def http_get(port, sni, host, path):
        seen.append((port, sni))
        return 200, ""

    result = doctor.check_webui(state, make_probes(http_get=http_get))
    assert result.status == "ok"
    assert seen == [(expected_port, expected_sni)]


def test_webui_502_is_warning():
    result = doctor.check_webui(AppState(), make_probes(http_get=lambda *a: (502, "")))
    assert result.status == "warn"
    assert "502" in result.detail


def test_webui_401_ok_with_basic_auth():
    probes = make_probes(http_get=lambda *a: (401, ""))
    assert doctor.check_webui(AppState(basic_auth_enabled=True), probes).status == "ok"
    assert doctor.check_webui(AppState(), probes).status == "warn"


def test_webui_400_ok_with_client_cert_auth():
    # nginx answers 400 when ssl_verify_client is on and no cert was sent.
    probes = make_probes(http_get=lambda *a: (400, ""))
    state = AppState(webui_client_cert_auth=True)
    assert doctor.check_webui(state, probes).status == "ok"
    assert doctor.check_webui(AppState(), probes).status == "warn"


def test_webui_detects_security_mitigation_lock():
    # TeddyCloud's lock answers 200 with a plaintext message — the status
    # code alone would report a healthy WebUI while the boxes are cut off.
    body = "TeddyCloud has been locked to mitigate security risks! Please check the logs"
    probes = make_probes(http_get=lambda *a: (200, body))
    result = doctor.check_webui(AppState(), probes)
    assert result.status == "fail"
    assert "LOCKED" in result.detail


def test_webui_detects_security_warning():
    body = "TeddyCloud has detected security risks! Please check the logs"
    probes = make_probes(http_get=lambda *a: (200, body))
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


# --- WebUI protection -------------------------------------------------------------


def test_webui_protection_none_warns():
    # Unprotected WebUIs trip TeddyCloud's security-mitigation lock (seen
    # in prod 2026-07: internet scanners → lock → boxes cut off).
    result = doctor.check_webui_protection(AppState())
    assert result.status == "warn"
    assert "locks itself" in result.detail


@pytest.mark.parametrize(
    "state",
    [
        AppState(basic_auth_enabled=True),
        AppState(webui_client_cert_auth=True),
        AppState(ip_allowlist=["192.168.0.0/24"]),
    ],
)
def test_webui_protection_any_mechanism_ok(state):
    assert doctor.check_webui_protection(state).status == "ok"


# --- image freshness ---------------------------------------------------------------


def test_image_freshness_up_to_date_ok():
    result = doctor.check_image_freshness(AppState(), make_probes())
    assert result.status == "ok"


def test_image_freshness_outdated_warns():
    probes = make_probes(remote_image_digest=lambda image: "sha256:newer")
    result = doctor.check_image_freshness(AppState(teddycloud_image_tag="develop"), probes)
    assert result.status == "warn"
    assert "Pull latest images" in result.detail


def test_image_freshness_checks_the_configured_channel():
    seen = []

    def local(image):
        seen.append(image)
        return "sha256:abc"

    probes = make_probes(local_image_digest=local)
    doctor.check_image_freshness(AppState(teddycloud_image_tag="develop"), probes)
    assert seen == [f"{doctor.TEDDYCLOUD_IMAGE}:develop"]


def test_image_freshness_degrades_without_data():
    no_local = make_probes(local_image_digest=lambda image: None)
    assert doctor.check_image_freshness(AppState(), no_local).status == "warn"
    no_remote = make_probes(remote_image_digest=lambda image: None)
    assert doctor.check_image_freshness(AppState(), no_remote).status == "warn"


def test_image_freshness_notes_running_and_latest_release():
    probes = make_probes(
        local_image_version=lambda image: "tc_v0.6.8",
        latest_teddycloud_release=lambda: "tc_v0.6.9",
    )
    result = doctor.check_image_freshness(AppState(teddycloud_image_tag="latest"), probes)
    assert "Running tc_v0.6.8" in result.detail
    assert "latest release tc_v0.6.9" in result.detail


def test_image_freshness_notes_running_is_latest():
    probes = make_probes(
        local_image_version=lambda image: "tc_v0.6.9",
        latest_teddycloud_release=lambda: "tc_v0.6.9",
    )
    result = doctor.check_image_freshness(AppState(teddycloud_image_tag="latest"), probes)
    assert "tc_v0.6.9 (latest release)" in result.detail


def test_image_freshness_develop_channel_skips_release_comparison():
    # On develop a GitHub release tag would be misleading; the digest above
    # is the measure, so only the running version is mentioned.
    probes = make_probes(
        local_image_version=lambda image: "tc_v0.7.0-dev",
        latest_teddycloud_release=lambda: "tc_v0.6.9",
    )
    result = doctor.check_image_freshness(AppState(teddycloud_image_tag="develop"), probes)
    assert "Running tc_v0.7.0-dev" in result.detail
    assert "latest release" not in result.detail


def test_image_freshness_version_note_absent_without_data():
    # The default probes return None for the version — no appended note.
    result = doctor.check_image_freshness(AppState(), make_probes())
    assert result.detail == "Running the newest 'latest' image."


# --- CA identity / backup ---------------------------------------------------------


def _install_ca(tmp_path, cert):
    ca_dir = tmp_path / "certs" / "server"
    ca_dir.mkdir(parents=True, exist_ok=True)
    (ca_dir / "ca.der").write_bytes(cert.public_bytes(serialization.Encoding.DER))


def test_ca_identity_records_on_first_sight(tmp_path):
    _install_ca(tmp_path, make_cert("TeddyCloud CA Root Cert.", "TeddyCloud CA Root Cert."))
    state = AppState()

    result = doctor.check_ca_identity(tmp_path, state)

    assert result.status == "ok"
    assert state.known_ca_fingerprint  # recorded


def test_ca_identity_unchanged_ok(tmp_path):
    _install_ca(tmp_path, make_cert("CA", "CA"))
    state = AppState()
    doctor.check_ca_identity(tmp_path, state)  # record

    assert doctor.check_ca_identity(tmp_path, state).status == "ok"


def test_ca_identity_change_fails(tmp_path):
    # The prod incident: regenerated certs mid-day silently broke every
    # flashed box. This must scream.
    _install_ca(tmp_path, make_cert("CA", "CA"))
    state = AppState()
    doctor.check_ca_identity(tmp_path, state)
    _install_ca(tmp_path, make_cert("CA", "CA"))  # new key, same name

    result = doctor.check_ca_identity(tmp_path, state)

    assert result.status == "fail"
    assert "CHANGED" in result.detail


def test_ca_identity_missing_ca_warns(tmp_path):
    assert doctor.check_ca_identity(tmp_path, AppState()).status == "warn"


def test_backup_none_warns(tmp_path):
    result = doctor.check_backup(tmp_path)
    assert result.status == "warn"
    assert "irreplaceable" in result.detail


def test_backup_fresh_ok(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "teddycloudhelper-backup-20260705-120000.tar.gz").write_bytes(b"x")
    assert doctor.check_backup(tmp_path).status == "ok"


def test_backup_stale_warns(tmp_path):
    import os
    import time

    backups = tmp_path / "backups"
    backups.mkdir()
    path = backups / "teddycloudhelper-backup-20260101-120000.tar.gz"
    path.write_bytes(b"x")
    old = time.time() - 60 * 86400
    os.utime(path, (old, old))

    result = doctor.check_backup(tmp_path)

    assert result.status == "warn"
    assert "60 day(s) old" in result.detail


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


LE_STATE = AppState(
    deployment_mode="nginx",
    webui_hostname="tc.example.com",
    webui_tls_mode="letsencrypt",
)


def _write_le_cert(tmp_path, days: int) -> None:
    live = tmp_path / "letsencrypt" / "live" / "tc.example.com"
    live.mkdir(parents=True)
    cert = make_cert("tc.example.com", "R11", issuer_org="Let's Encrypt", days=days)
    (live / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_letsencrypt_missing_cert_warns(tmp_path):
    result = doctor.check_letsencrypt(tmp_path, LE_STATE, make_probes())
    assert result.status == "warn"
    assert "no certificate" in result.detail.lower()


def test_letsencrypt_valid_cert_ok(tmp_path):
    _write_le_cert(tmp_path, days=60)
    result = doctor.check_letsencrypt(tmp_path, LE_STATE, make_probes())
    assert result.status == "ok"


def test_letsencrypt_expiring_cert_warns(tmp_path):
    _write_le_cert(tmp_path, days=10)
    result = doctor.check_letsencrypt(tmp_path, LE_STATE, make_probes())
    assert result.status == "warn"
    assert "renews at 30 days" in result.detail


def test_letsencrypt_expired_cert_fails(tmp_path):
    _write_le_cert(tmp_path, days=-1)
    result = doctor.check_letsencrypt(tmp_path, LE_STATE, make_probes())
    assert result.status == "fail"
    assert "EXPIRED" in result.detail


def test_letsencrypt_falls_back_to_live_cert(tmp_path, monkeypatch):
    # ./letsencrypt is typically root-owned — the check must degrade to the
    # certificate nginx actually serves instead of demanding sudo.
    from teddycloudhelper.certs import letsencrypt as le_mod
    from teddycloudhelper.certs.ca import CertError

    def denied(*args, **kwargs):
        raise CertError("permission denied")

    monkeypatch.setattr(le_mod, "cert_expiry", denied)
    probes = make_probes(
        tls_cert=lambda port, sni: make_cert("tc.example.com", "R11", days=60)
    )
    result = doctor.check_letsencrypt(tmp_path, LE_STATE, probes)
    assert result.status == "ok"
    assert "live WebUI certificate" in result.detail


# --- known boxes -------------------------------------------------------------------


def test_boxes_listed_ok():
    result = doctor.check_boxes(make_probes())
    assert result.status == "ok"
    assert "Kids" in result.detail


def test_boxes_none_warns():
    result = doctor.check_boxes(make_probes(getboxes=lambda: '{"boxes":[]}'))
    assert result.status == "warn"
    assert "no box" in result.detail.lower()


def test_boxes_query_failure_warns():
    probes = make_probes(getboxes=_raise(docker_cli.DockerError("container not running")))
    assert doctor.check_boxes(probes).status == "warn"


def test_boxes_garbage_answer_warns():
    probes = make_probes(getboxes=lambda: "<html>404</html>")
    assert doctor.check_boxes(probes).status == "warn"


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
    _install_ca(tmp_path, make_cert("CA", "CA"))
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "teddycloudhelper-backup-20260705-120000.tar.gz").write_bytes(b"x")
    results = doctor.run_checks(tmp_path, AppState(basic_auth_enabled=True), make_probes())
    assert all(r.status == "ok" for r in results), [
        (r.name, r.detail) for r in results if r.status != "ok"
    ]
