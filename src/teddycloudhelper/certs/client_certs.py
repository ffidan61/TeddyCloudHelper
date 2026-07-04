"""Issue and list browser client certificates for WebUI access.

Each cert lives in ``webui-pki/clients/<name>/`` as ``.crt``/``.key`` (PEM)
plus a password-protected ``.p12`` bundle (cert + key + CA) for browser
import. Serial numbers come from ``AppState.next_serial`` — the caller (menu)
passes the serial and persists the incremented counter; revocation is done
per serial in :mod:`crl`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from teddycloudhelper.certs import ca as ca_mod
from teddycloudhelper.certs.ca import CertError

CLIENT_VALIDITY_DAYS = 1095
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class ClientCertInfo:
    """One issued client cert, read live from disk (never persisted)."""

    name: str
    common_name: str
    serial: int
    not_valid_after: datetime
    cert_path: Path
    p12_path: Path


def clients_dir(project_dir: Path) -> Path:
    return ca_mod.pki_dir(project_dir) / "clients"


def client_dir(project_dir: Path, name: str) -> Path:
    return clients_dir(project_dir) / name


def issue_client_cert(
    project_dir: Path,
    name: str,
    serial: int,
    p12_password: str,
    days: int = CLIENT_VALIDITY_DAYS,
) -> ClientCertInfo:
    """Issue a browser client cert signed by the WebUI CA."""
    if not _NAME_RE.match(name):
        raise CertError(
            f"Invalid name {name!r}: use letters, digits, '.', '_' or '-' "
            "(it becomes a directory and file name)."
        )
    ca_cert, ca_key = ca_mod.load_ca(project_dir)
    key = ca_mod.generate_key()
    now = ca_mod.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    target = client_dir(project_dir, name)
    cert_path = target / f"{name}.crt"
    ca_mod.write_key(target / f"{name}.key", key)
    ca_mod.write_cert(cert_path, cert)

    encryption: serialization.KeySerializationEncryption
    if p12_password:
        encryption = serialization.BestAvailableEncryption(p12_password.encode())
    else:
        encryption = serialization.NoEncryption()
    p12_path = target / f"{name}.p12"
    p12_path.write_bytes(
        pkcs12.serialize_key_and_certificates(
            name.encode(), key, cert, cas=[ca_cert], encryption_algorithm=encryption
        )
    )
    return _info(name, cert, cert_path, p12_path)


def load_client_cert(project_dir: Path, name: str) -> x509.Certificate:
    path = client_dir(project_dir, name) / f"{name}.crt"
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except FileNotFoundError:
        raise CertError(
            f"No client certificate named {name!r} in {clients_dir(project_dir)}."
        ) from None
    except ValueError as exc:
        raise CertError(f"{path} is not a valid PEM certificate: {exc}") from exc


def list_client_certs(project_dir: Path) -> list[ClientCertInfo]:
    """Scan webui-pki/clients/ live; unreadable entries are skipped."""
    root = clients_dir(project_dir)
    if not root.is_dir():
        return []
    infos = []
    for entry in sorted(root.iterdir()):
        cert_path = entry / f"{entry.name}.crt"
        if not cert_path.is_file():
            continue
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        except ValueError:
            continue
        infos.append(_info(entry.name, cert, cert_path, entry / f"{entry.name}.p12"))
    return infos


def _info(
    name: str, cert: x509.Certificate, cert_path: Path, p12_path: Path
) -> ClientCertInfo:
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return ClientCertInfo(
        name=name,
        common_name=str(cns[0].value) if cns else "",
        serial=cert.serial_number,
        not_valid_after=cert.not_valid_after_utc,
        cert_path=cert_path,
        p12_path=p12_path,
    )
