"""Own certificate authority for WebUI client-certificate access.

This CA only guards the WebUI port (mTLS terminated by nginx). It never
touches the box path — port 443 stays raw TLS passthrough to TeddyCloud.

Layout inside a project directory (``webui-pki/``):

    webui-pki/
    ├── ca/ca.crt, ca.key      # this module
    ├── crl/ca.crl             # crl.py (nginx `ssl_crl`)
    ├── clients/<name>/        # client_certs.py
    └── server/                # server_certs.py (self-signed WebUI cert)

The CA key is stored unencrypted (chmod 600 where the OS supports it) so
that issuing and revoking never needs a passphrase prompt.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PKI_DIRNAME = "webui-pki"
DEFAULT_CA_CN = "TeddyCloudHelper WebUI CA"
CA_VALIDITY_DAYS = 3650


class CertError(Exception):
    """A certificate operation failed."""


def pki_dir(project_dir: Path) -> Path:
    return project_dir / PKI_DIRNAME


def ca_cert_path(project_dir: Path) -> Path:
    return pki_dir(project_dir) / "ca" / "ca.crt"


def ca_key_path(project_dir: Path) -> Path:
    return pki_dir(project_dir) / "ca" / "ca.key"


def ca_exists(project_dir: Path) -> bool:
    return ca_cert_path(project_dir).is_file() and ca_key_path(project_dir).is_file()


# --- shared primitives (also used by the sibling cert modules) ---------------


def generate_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    # Best effort; meaningless on Windows.
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def write_cert(path: Path, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- CA lifecycle -------------------------------------------------------------


def create_ca(
    project_dir: Path,
    common_name: str = DEFAULT_CA_CN,
    days: int = CA_VALIDITY_DAYS,
) -> Path:
    """Create the WebUI CA. Refuses to overwrite an existing one."""
    if ca_exists(project_dir):
        raise CertError(
            f"A WebUI CA already exists in {pki_dir(project_dir)}. "
            "Delete it manually if you really want to start over "
            "(all issued client certs become useless)."
        )
    key = generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    write_key(ca_key_path(project_dir), key)
    write_cert(ca_cert_path(project_dir), cert)
    return ca_cert_path(project_dir)


def load_ca(project_dir: Path) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    if not ca_exists(project_dir):
        raise CertError(
            f"No WebUI CA in {pki_dir(project_dir)} yet — create one first."
        )
    cert = x509.load_pem_x509_certificate(ca_cert_path(project_dir).read_bytes())
    key = serialization.load_pem_private_key(
        ca_key_path(project_dir).read_bytes(), password=None
    )
    return cert, key
