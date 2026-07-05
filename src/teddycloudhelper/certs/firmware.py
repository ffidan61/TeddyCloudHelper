"""Inspect patched firmware images before flashing.

A patched ESP32 image embeds two things that decide whether the box will
ever connect: the CA it will trust (must be THIS instance's
``certs/server/ca.der``) and its own original Boxine client certificate.
Flashing an image patched by another instance produces nothing but silent
TLS handshake failures — verified the hard way in prod (2026-07). This
module finds every DER certificate inside an image and compares the
embedded CA against the instance CA.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

from teddycloudhelper.certs import server_certs
from teddycloudhelper.certs.ca import CertError

# DER SEQUENCE header with 2-byte length; plausible certificate sizes.
_MIN_CERT_LEN = 300
_MAX_CERT_LEN = 4000


@dataclass
class ImageCheck:
    """Result of comparing an image against this instance."""

    certificates: list[x509.Certificate]
    ca_match: bool | None  # None: image contains no CA to compare
    instance_ca_serial: int
    box_cert_cn: str | None  # CN of the embedded Boxine client cert


def _cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else name.rfc4514_string()


def list_images(project_dir: Path) -> list[Path]:
    """.bin files in the project's firmware/ bind mount, newest first.

    TeddyCloud stores uploaded dumps and patched images there, so this is
    where the file to check usually already lives.
    """
    directory = project_dir / "firmware"
    if not directory.is_dir():
        return []
    return sorted(
        directory.glob("*.bin"), key=lambda p: p.stat().st_mtime_ns, reverse=True
    )


def scan_certificates(image_path: Path) -> list[x509.Certificate]:
    """Every unique DER certificate embedded in *image_path*."""
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        raise CertError(f"Cannot read {image_path}: {exc}") from exc
    found: dict[bytes, x509.Certificate] = {}
    pos = 0
    while True:
        pos = data.find(b"\x30\x82", pos)
        if pos == -1:
            break
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if _MIN_CERT_LEN <= length <= _MAX_CERT_LEN:
            blob = data[pos : pos + 4 + length]
            try:
                cert = x509.load_der_x509_certificate(blob)
            except ValueError:
                pass
            else:
                found.setdefault(blob, cert)
        pos += 2
    return list(found.values())


def check_image(project_dir: Path, image_path: Path) -> ImageCheck:
    """Compare the certificates inside *image_path* with this instance."""
    instance_ca = server_certs.load_box_ca(project_dir)
    if instance_ca is None:
        raise CertError(
            f"{server_certs.box_ca_path(project_dir)} not found — TeddyCloud "
            "generates it on first container start. Start the services once, "
            "then retry."
        )
    certificates = scan_certificates(image_path)

    ca_match: bool | None = None
    box_cert_cn: str | None = None
    for cert in certificates:
        if "Boxine" in cert.issuer.rfc4514_string():
            box_cert_cn = _cn(cert.subject)
        elif cert.subject == cert.issuer:  # a self-signed CA candidate
            # Certificate equality compares the exact DER bytes; one exact
            # match wins even if stale CA remnants are also present.
            ca_match = ca_match or (cert == instance_ca)
    return ImageCheck(
        certificates=certificates,
        ca_match=ca_match,
        instance_ca_serial=instance_ca.serial_number,
        box_cert_cn=box_cert_cn,
    )
