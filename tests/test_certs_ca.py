import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

from teddycloudhelper.certs import ca


def test_create_ca_writes_cert_and_key(tmp_path):
    path = ca.create_ca(tmp_path)

    assert path == ca.ca_cert_path(tmp_path)
    assert ca.ca_exists(tmp_path)
    cert, key = ca.load_ca(tmp_path)
    assert cert.subject == cert.issuer  # self-signed
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert cn == ca.DEFAULT_CA_CN
    basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.value.ca is True
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and usage.crl_sign


def test_create_ca_custom_cn(tmp_path):
    ca.create_ca(tmp_path, common_name="My CA")
    cert, _ = ca.load_ca(tmp_path)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "My CA"


def test_create_ca_refuses_overwrite(tmp_path):
    ca.create_ca(tmp_path)
    with pytest.raises(ca.CertError, match="already exists"):
        ca.create_ca(tmp_path)


def test_load_ca_missing_raises(tmp_path):
    with pytest.raises(ca.CertError, match="No WebUI CA"):
        ca.load_ca(tmp_path)


def test_ca_exists_requires_both_files(tmp_path):
    ca.create_ca(tmp_path)
    ca.ca_key_path(tmp_path).unlink()
    assert not ca.ca_exists(tmp_path)
