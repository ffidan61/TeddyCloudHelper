"""WebUI server certificate + export of TeddyCloud's box CA.

Two unrelated jobs that both concern "server" certs:

* ``create_webui_server_cert`` — self-signed cert for the nginx-terminated
  WebUI port (``webui-pki/server/``). Only for browsers; never for the box.
* ``export_box_ca`` — copy TeddyCloud's auto-generated ``certs/server/ca.der``
  somewhere convenient so it can be flashed onto the box.
"""

from __future__ import annotations

import ipaddress
import shutil
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from teddycloudhelper.certs import ca as ca_mod
from teddycloudhelper.certs.ca import CertError

WEBUI_CERT_VALIDITY_DAYS = 1095


def server_dir(project_dir: Path) -> Path:
    return ca_mod.pki_dir(project_dir) / "server"


def create_webui_server_cert(
    project_dir: Path, hostname: str, days: int = WEBUI_CERT_VALIDITY_DAYS
) -> Path:
    """Self-signed HTTPS cert for the WebUI (SAN = hostname or IP)."""
    hostname = hostname.strip()
    if not hostname:
        raise CertError("Hostname must not be empty.")
    try:
        san: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(hostname))
    except ValueError:
        san = x509.DNSName(hostname)

    key = ca_mod.generate_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = ca_mod.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = server_dir(project_dir) / "server.crt"
    ca_mod.write_key(server_dir(project_dir) / "server.key", key)
    ca_mod.write_cert(cert_path, cert)
    return cert_path


def webui_cert_matches(project_dir: Path, hostname: str) -> bool:
    """True if an existing WebUI cert covers *hostname*; False if none/stale."""
    path = server_dir(project_dir) / "server.crt"
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except (FileNotFoundError, ValueError):
        return False
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    names = set(san.get_values_for_type(x509.DNSName))
    names.update(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
    return hostname.strip() in names


# --- box CA export ------------------------------------------------------------


def box_ca_path(project_dir: Path) -> Path:
    """TeddyCloud's auto-generated CA (DER) that must be flashed onto the box."""
    return project_dir / "certs" / "server" / "ca.der"


def export_box_ca(project_dir: Path, destination: Path) -> Path:
    """Validate and copy ``certs/server/ca.der`` to *destination*."""
    source = box_ca_path(project_dir)
    try:
        data = source.read_bytes()
    except FileNotFoundError:
        raise CertError(
            f"{source} not found — TeddyCloud generates it on first container "
            "start. Start the services once, then retry."
        ) from None
    try:
        x509.load_der_x509_certificate(data)
    except ValueError as exc:
        raise CertError(f"{source} is not a valid DER certificate: {exc}") from exc
    if destination.is_dir():
        destination = destination / "ca.der"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination
