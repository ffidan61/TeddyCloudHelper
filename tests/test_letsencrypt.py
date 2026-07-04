from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from teddycloudhelper.certs import ca, letsencrypt
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.state import AppState


def write_le_cert(tmp_path, hostname, days):
    """Plant a cert with the given remaining validity (may be negative) at
    the LE live path."""
    key = ca.generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = ca.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=100))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    live = letsencrypt.live_cert_dir(tmp_path, hostname)
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_validate_hostname_accepts_public_names():
    assert letsencrypt.validate_hostname(" Teddy.Example.COM ") == "teddy.example.com"


@pytest.mark.parametrize(
    ("bad", "match"),
    [
        ("", "No WebUI hostname"),
        ("192.168.1.10", "IP address"),
        ("2001:db8::1", "IP address"),
        ("*.example.com", "Wildcard"),
        ("teddycloud", "not a public DNS name"),
        ("teddycloud.local", "not a public DNS name"),
        ("box.home.arpa", "not a public DNS name"),
        ("nas.lan", "not a public DNS name"),
    ],
)
def test_validate_hostname_rejects(bad, match):
    with pytest.raises(CertError, match=match):
        letsencrypt.validate_hostname(bad)


def test_certonly_args():
    args = letsencrypt.certonly_args("tc.example.com")
    assert args[0] == "certonly"
    assert "--webroot" in args
    assert args[args.index("--domain") + 1] == "tc.example.com"
    # LE sends no expiry mails anymore — no account email is registered
    assert "--register-unsafely-without-email" in args
    assert "--email" not in args
    assert "--non-interactive" in args
    assert "--agree-tos" in args


def test_cert_exists(tmp_path):
    assert not letsencrypt.cert_exists(tmp_path, "tc.example.com")
    live = letsencrypt.live_cert_dir(tmp_path, "tc.example.com")
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_text("pem")
    assert letsencrypt.cert_exists(tmp_path, "tc.example.com")


# --- expiry monitoring (LE sends no expiry emails anymore) ---------------------


def le_state():
    return AppState(webui_tls_mode="letsencrypt", webui_hostname="tc.example.com")


def test_cert_expiry_none_without_cert(tmp_path):
    assert letsencrypt.cert_expiry(tmp_path, "tc.example.com") is None


def test_cert_expiry_reads_leaf(tmp_path):
    write_le_cert(tmp_path, "tc.example.com", days=90)
    expiry = letsencrypt.cert_expiry(tmp_path, "tc.example.com")
    assert expiry is not None
    assert (expiry - ca.utcnow()).days in (89, 90)


def test_cert_expiry_garbage_raises(tmp_path):
    live = letsencrypt.live_cert_dir(tmp_path, "tc.example.com")
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_text("garbage")
    with pytest.raises(CertError, match="not a valid PEM"):
        letsencrypt.cert_expiry(tmp_path, "tc.example.com")


def test_renewal_warning_not_in_le_mode(tmp_path):
    assert letsencrypt.renewal_warning(tmp_path, AppState()) is None


def test_renewal_warning_missing_cert(tmp_path):
    warning = letsencrypt.renewal_warning(tmp_path, le_state())
    assert warning is not None and "no certificate was found" in warning


def test_renewal_warning_healthy_cert(tmp_path):
    write_le_cert(tmp_path, "tc.example.com", days=60)
    assert letsencrypt.renewal_warning(tmp_path, le_state()) is None


def test_renewal_warning_expiring_cert(tmp_path):
    write_le_cert(tmp_path, "tc.example.com", days=10)
    warning = letsencrypt.renewal_warning(tmp_path, le_state())
    assert warning is not None and "renewal seems to be failing" in warning


def test_renewal_warning_expired_cert(tmp_path):
    write_le_cert(tmp_path, "tc.example.com", days=-2)
    warning = letsencrypt.renewal_warning(tmp_path, le_state())
    assert warning is not None and "EXPIRED" in warning


def test_renewal_warning_degrades_on_read_error(tmp_path, monkeypatch):
    # e.g. ./letsencrypt is root-owned (certbot container) — the warning must
    # surface the problem instead of crashing or staying silent
    def boom(*a, **kw):
        raise CertError("permission denied reading fullchain.pem")

    monkeypatch.setattr(letsencrypt, "cert_expiry", boom)
    warning = letsencrypt.renewal_warning(tmp_path, le_state())
    assert warning is not None and "Could not check" in warning


# --- challenge self-test --------------------------------------------------------


def serve_directory(directory):
    import http.server
    import threading
    from functools import partial

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_probe_succeeds_via_local_server(tmp_path):
    server = serve_directory(tmp_path / "certbot-www")
    (tmp_path / "certbot-www").mkdir()
    try:
        assert (
            letsencrypt.probe_http_challenge(
                tmp_path, "127.0.0.1", port=server.server_address[1]
            )
            is None
        )
    finally:
        server.shutdown()


def test_probe_reports_unreachable(tmp_path):
    problem = letsencrypt.probe_http_challenge(
        tmp_path, "127.0.0.1", timeout=2, port=1  # nothing listens on port 1
    )
    assert problem is not None and "was not reachable" in problem
