"""Firmware-image inspection: embedded CA vs instance CA (the 2026-07 lesson)."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from teddycloudhelper.certs import firmware
from teddycloudhelper.certs.ca import CertError


def make_cert(subject_cn: str, issuer_cn: str | None = None, issuer_org: str | None = None):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    if issuer_cn is None:
        issuer = subject  # self-signed
    else:
        attrs = [x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]
        if issuer_org:
            attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, issuer_org))
        issuer = x509.Name(attrs)
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )


def der(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def make_image(tmp_path, *certs, padding: int = 512):
    """A fake flash image: certificate DER blobs between junk bytes."""
    blob = b"\xff" * padding
    for cert in certs:
        blob += der(cert) + b"\x00" * padding
    path = tmp_path / "image.bin"
    path.write_bytes(blob)
    return path


def install_instance_ca(tmp_path, cert) -> None:
    ca_dir = tmp_path / "certs" / "server"
    ca_dir.mkdir(parents=True)
    (ca_dir / "ca.der").write_bytes(der(cert))


def test_scan_finds_embedded_certs(tmp_path):
    ca = make_cert("TeddyCloud CA Root Cert.")
    box = make_cert("AABBCCDDEEFF", "Boxine Factory SubCA 14", "Boxine GmbH")
    image = make_image(tmp_path, ca, box)
    found = firmware.scan_certificates(image)
    assert {c.serial_number for c in found} == {ca.serial_number, box.serial_number}


def test_check_image_matching_ca(tmp_path):
    ca = make_cert("TeddyCloud CA Root Cert.")
    box = make_cert("AABBCCDDEEFF", "Boxine Factory SubCA 14", "Boxine GmbH")
    install_instance_ca(tmp_path, ca)
    image = make_image(tmp_path, ca, box)

    result = firmware.check_image(tmp_path, image)

    assert result.ca_match is True
    assert result.box_cert_cn == "AABBCCDDEEFF"
    assert result.instance_ca_serial == ca.serial_number


def test_check_image_foreign_ca(tmp_path):
    # The prod incident: image patched by ANOTHER instance — same CA name,
    # different key/serial. Must be flagged, never flashed.
    instance_ca = make_cert("TeddyCloud CA Root Cert.")
    foreign_ca = make_cert("TeddyCloud CA Root Cert.")
    install_instance_ca(tmp_path, instance_ca)
    image = make_image(tmp_path, foreign_ca)

    result = firmware.check_image(tmp_path, image)

    assert result.ca_match is False


def test_check_image_without_ca(tmp_path):
    # Only the Boxine cert (e.g. an unpatched dump): no CA to compare.
    instance_ca = make_cert("TeddyCloud CA Root Cert.")
    box = make_cert("AABBCCDDEEFF", "Boxine Factory SubCA 14", "Boxine GmbH")
    install_instance_ca(tmp_path, instance_ca)
    image = make_image(tmp_path, box)

    result = firmware.check_image(tmp_path, image)

    assert result.ca_match is None
    assert result.box_cert_cn == "AABBCCDDEEFF"


def test_list_images_newest_first(tmp_path):
    import os

    fw = tmp_path / "firmware"
    fw.mkdir()
    old = fw / "ESP32_old.bin"
    new = fw / "ESP32_new_patched.bin"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (1, 1))
    (fw / "notes.txt").write_text("ignored")

    assert firmware.list_images(tmp_path) == [new, old]


def test_list_images_without_dir(tmp_path):
    assert firmware.list_images(tmp_path) == []


def test_check_image_needs_instance_ca(tmp_path):
    image = make_image(tmp_path, make_cert("whatever"))
    with pytest.raises(CertError, match="first container start"):
        firmware.check_image(tmp_path, image)
