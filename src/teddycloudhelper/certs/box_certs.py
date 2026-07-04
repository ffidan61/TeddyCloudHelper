"""Validate and install the certificates dumped from a Toniebox.

The dump consists of ``ca.der`` (Boxine CA), ``client.der`` and
``private.der``. TeddyCloud needs them in ``certs/client/`` to talk to the
real Boxine cloud on the box's behalf. They cannot be regenerated — existing
files are backed up before anything is overwritten.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from teddycloudhelper.certs.ca import CertError

BOX_CERT_NAMES = ("ca.der", "client.der", "private.der")


@dataclass
class BoxCertInfo:
    """Result of inspecting a dumped cert set."""

    client_common_name: str
    issuer: str
    not_valid_after: datetime
    key_matches_cert: bool


def inspect_box_certs(ca_der: Path, client_der: Path, private_der: Path) -> BoxCertInfo:
    """Parse a dumped cert set and check that the private key fits the cert."""
    _load_der_cert(ca_der)  # only validated, box identity comes from client.der
    client = _load_der_cert(client_der)
    try:
        key = serialization.load_der_private_key(private_der.read_bytes(), password=None)
    except FileNotFoundError:
        raise CertError(f"{private_der} not found.") from None
    except ValueError as exc:
        raise CertError(f"{private_der} is not a valid DER private key: {exc}") from exc

    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    matches = key.public_key().public_bytes(
        serialization.Encoding.DER, spki
    ) == client.public_key().public_bytes(serialization.Encoding.DER, spki)

    return BoxCertInfo(
        client_common_name=client.subject.rfc4514_string(),
        issuer=client.issuer.rfc4514_string(),
        not_valid_after=client.not_valid_after_utc,
        key_matches_cert=matches,
    )


def install_box_certs(
    project_dir: Path, ca_der: Path, client_der: Path, private_der: Path
) -> list[Path]:
    """Copy the dumped set into ``certs/client/``, backing up existing files."""
    info = inspect_box_certs(ca_der, client_der, private_der)
    if not info.key_matches_cert:
        raise CertError(
            "private.der does not match client.der — this set would not "
            "authenticate. Check that all three files come from the same dump."
        )
    dest_dir = project_dir / "certs" / "client"
    dest_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for source, name in zip((ca_der, client_der, private_der), BOX_CERT_NAMES, strict=True):
        dest = dest_dir / name
        if dest.is_file():
            shutil.copy2(dest, dest.with_name(f"{name}.{stamp}.bak"))
        shutil.copy2(source, dest)
        installed.append(dest)
    return installed


def _load_der_cert(path: Path) -> x509.Certificate:
    try:
        return x509.load_der_x509_certificate(path.read_bytes())
    except FileNotFoundError:
        raise CertError(f"{path} not found.") from None
    except ValueError as exc:
        raise CertError(f"{path} is not a valid DER certificate: {exc}") from exc
