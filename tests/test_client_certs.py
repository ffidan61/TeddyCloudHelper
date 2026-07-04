import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

from teddycloudhelper.certs import ca, client_certs


@pytest.fixture
def project(tmp_path):
    ca.create_ca(tmp_path)
    return tmp_path


def test_issue_writes_crt_key_p12(project):
    info = client_certs.issue_client_cert(project, "alice", serial=1, p12_password="secret")

    target = client_certs.client_dir(project, "alice")
    assert (target / "alice.crt").is_file()
    assert (target / "alice.key").is_file()
    assert info.p12_path == target / "alice.p12"
    assert info.p12_path.is_file()
    assert info.serial == 1
    assert info.common_name == "alice"


def test_issued_cert_is_signed_by_ca_and_client_auth(project):
    client_certs.issue_client_cert(project, "alice", serial=5, p12_password="pw")

    cert = client_certs.load_client_cert(project, "alice")
    ca_cert, _ = ca.load_ca(project)
    cert.verify_directly_issued_by(ca_cert)  # raises if not signed by our CA
    assert cert.serial_number == 5
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert basic.ca is False


def test_p12_bundle_opens_with_password(project):
    client_certs.issue_client_cert(project, "alice", serial=1, p12_password="secret")

    data = client_certs.client_dir(project, "alice").joinpath("alice.p12").read_bytes()
    key, cert, extra = pkcs12.load_key_and_certificates(data, b"secret")
    assert key is not None and cert is not None
    assert len(extra) == 1  # CA cert is bundled for the browser


def test_p12_without_password(project):
    client_certs.issue_client_cert(project, "bob", serial=2, p12_password="")
    data = client_certs.client_dir(project, "bob").joinpath("bob.p12").read_bytes()
    key, cert, _ = pkcs12.load_key_and_certificates(data, None)
    assert key is not None and cert is not None


@pytest.mark.parametrize("bad", ["", "../evil", "a b", ".hidden", "sla/sh"])
def test_invalid_names_rejected(project, bad):
    with pytest.raises(client_certs.CertError, match="Invalid name"):
        client_certs.issue_client_cert(project, bad, serial=1, p12_password="pw")


def test_issue_without_ca_raises(tmp_path):
    with pytest.raises(client_certs.CertError, match="No WebUI CA"):
        client_certs.issue_client_cert(tmp_path, "alice", serial=1, p12_password="pw")


def test_list_client_certs(project):
    assert client_certs.list_client_certs(project) == []
    client_certs.issue_client_cert(project, "alice", serial=1, p12_password="pw")
    client_certs.issue_client_cert(project, "bob", serial=2, p12_password="pw")

    infos = client_certs.list_client_certs(project)

    assert [(i.name, i.serial) for i in infos] == [("alice", 1), ("bob", 2)]


def test_list_skips_broken_entries(project):
    client_certs.issue_client_cert(project, "alice", serial=1, p12_password="pw")
    broken = client_certs.client_dir(project, "broken")
    broken.mkdir()
    (broken / "broken.crt").write_text("not a cert")

    assert [i.name for i in client_certs.list_client_certs(project)] == ["alice"]


def test_load_missing_cert_raises(project):
    with pytest.raises(client_certs.CertError, match="No client certificate"):
        client_certs.load_client_cert(project, "ghost")
