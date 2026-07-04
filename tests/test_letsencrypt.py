import pytest

from teddycloudhelper.certs import ca, letsencrypt
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.state import AppState


def write_le_cert(tmp_path, hostname, days):
    """Plant a cert with the given remaining validity at the LE live path."""
    ca.create_ca(tmp_path, days=days)
    live = letsencrypt.live_cert_dir(tmp_path, hostname)
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_bytes(ca.ca_cert_path(tmp_path).read_bytes())


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


def test_validate_email():
    assert letsencrypt.validate_email(" a@b.de ") == "a@b.de"
    for bad in ("", "nope", "@b.de", "a@"):
        with pytest.raises(CertError, match="email"):
            letsencrypt.validate_email(bad)


def test_certonly_args():
    args = letsencrypt.certonly_args("tc.example.com", "a@b.de")
    assert args[0] == "certonly"
    assert "--webroot" in args
    assert args[args.index("--domain") + 1] == "tc.example.com"
    assert args[args.index("--email") + 1] == "a@b.de"
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
