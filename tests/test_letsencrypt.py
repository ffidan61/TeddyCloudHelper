import pytest

from teddycloudhelper.certs import letsencrypt
from teddycloudhelper.certs.ca import CertError


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
