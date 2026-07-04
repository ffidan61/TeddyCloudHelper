"""Box-cert tests build a fake "dump" (CA + client cert + key, DER) on the fly."""

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from teddycloudhelper.certs import box_certs
from teddycloudhelper.certs.ca import utcnow


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _make_cert(subject_cn, issuer_cn, public_key, signing_key):
    now = utcnow()
    return (
        x509.CertificateBuilder()
        .subject_name(_name(subject_cn))
        .issuer_name(_name(issuer_cn))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=365))
        .sign(signing_key, hashes.SHA256())
    )


@pytest.fixture
def dump(tmp_path):
    """Write ca.der / client.der / private.der like a real box dump."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    client_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _make_cert("Boxine CA", "Boxine CA", ca_key.public_key(), ca_key)
    client_cert = _make_cert("b'001122334455'", "Boxine CA", client_key.public_key(), ca_key)

    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()
    (dump_dir / "ca.der").write_bytes(ca_cert.public_bytes(serialization.Encoding.DER))
    (dump_dir / "client.der").write_bytes(client_cert.public_bytes(serialization.Encoding.DER))
    (dump_dir / "private.der").write_bytes(
        client_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return dump_dir


def _paths(dump_dir):
    return dump_dir / "ca.der", dump_dir / "client.der", dump_dir / "private.der"


def test_inspect_valid_dump(dump):
    info = box_certs.inspect_box_certs(*_paths(dump))

    assert "001122334455" in info.client_common_name
    assert "Boxine CA" in info.issuer
    assert info.key_matches_cert


def test_inspect_detects_wrong_key(dump):
    other_key = ec.generate_private_key(ec.SECP256R1())
    (dump / "private.der").write_bytes(
        other_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    assert not box_certs.inspect_box_certs(*_paths(dump)).key_matches_cert


def test_inspect_missing_file(dump):
    (dump / "client.der").unlink()
    with pytest.raises(box_certs.CertError, match="not found"):
        box_certs.inspect_box_certs(*_paths(dump))


def test_inspect_invalid_der(dump):
    (dump / "ca.der").write_text("garbage")
    with pytest.raises(box_certs.CertError, match="not a valid DER certificate"):
        box_certs.inspect_box_certs(*_paths(dump))


def test_install_copies_all_three(dump, tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    installed = box_certs.install_box_certs(project, *_paths(dump))

    dest = project / "certs" / "client"
    assert installed == [dest / name for name in box_certs.BOX_CERT_NAMES]
    for name in box_certs.BOX_CERT_NAMES:
        assert (dest / name).read_bytes() == (dump / name).read_bytes()


def test_install_backs_up_existing(dump, tmp_path):
    project = tmp_path / "project"
    dest = project / "certs" / "client"
    dest.mkdir(parents=True)
    (dest / "client.der").write_bytes(b"old contents")

    box_certs.install_box_certs(project, *_paths(dump))

    backups = list(dest.glob("client.der.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old contents"
    assert (dest / "client.der").read_bytes() == (dump / "client.der").read_bytes()


def test_install_refuses_mismatched_key(dump, tmp_path):
    other_key = ec.generate_private_key(ec.SECP256R1())
    (dump / "private.der").write_bytes(
        other_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(box_certs.CertError, match="does not match"):
        box_certs.install_box_certs(project, *_paths(dump))
    assert not (project / "certs" / "client").exists()
