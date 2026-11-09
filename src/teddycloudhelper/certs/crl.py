"""Certificate revocation list for nginx ``ssl_crl``.

TeddyCloud itself has no CRL support (see CLAUDE.md) — only nginx consumes
this file when it terminates the WebUI port. The CRL file *is* the store of
revoked serials: every change reloads the existing entries and re-signs the
whole list, so nothing needs to be persisted in ``AppState``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from teddycloudhelper.certs import ca as ca_mod
from teddycloudhelper.certs.ca import CertError

# nginx only checks the signature and the revoked serials; regenerate long
# before this ever matters (the file is rewritten on every revocation anyway).
CRL_NEXT_UPDATE_DAYS = 3650


def crl_path(project_dir: Path) -> Path:
    return ca_mod.pki_dir(project_dir) / "crl" / "ca.crl"


def revoked_serials(project_dir: Path) -> list[int]:
    """Serials in the current CRL, read live. Empty if no CRL exists yet."""
    return [serial for serial, _ in _load_entries(project_dir)]


def ensure_crl(project_dir: Path) -> Path:
    """Create an empty CRL if none exists (nginx needs the file to start) —
    or if the existing one was signed by a previous CA. nginx would reject
    every client certificate of the new CA against such a stale CRL."""
    path = crl_path(project_dir)
    if not path.is_file() or not _signed_by_current_ca(project_dir):
        _write(project_dir, [])
    return path


def revoke_serial(project_dir: Path, serial: int) -> Path:
    """Add *serial* to the CRL (idempotent) and re-sign it."""
    entries = _load_entries(project_dir)
    if all(existing != serial for existing, _ in entries):
        entries.append((serial, ca_mod.utcnow()))
    return _write(project_dir, entries)


def _load_crl(project_dir: Path) -> x509.CertificateRevocationList | None:
    path = crl_path(project_dir)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return x509.load_pem_x509_crl(data)
    except ValueError as exc:
        raise CertError(f"{path} is not a valid PEM CRL: {exc}") from exc


def _signed_by_current_ca(project_dir: Path) -> bool:
    """False when the CRL belongs to an earlier (deleted and re-created) CA.
    True when there is no CA to compare against."""
    crl = _load_crl(project_dir)
    if crl is None or not ca_mod.ca_exists(project_dir):
        return True
    ca_cert, _ = ca_mod.load_ca(project_dir)
    return crl.issuer == ca_cert.subject and crl.is_signature_valid(ca_cert.public_key())


def _load_entries(project_dir: Path) -> list[tuple[int, datetime]]:
    crl = _load_crl(project_dir)
    if crl is None or not _signed_by_current_ca(project_dir):
        # Serials revoked under a previous CA mean nothing for the current
        # one (and could collide with its new serials).
        return []
    return [(entry.serial_number, entry.revocation_date_utc) for entry in crl]


def _write(project_dir: Path, entries: Iterable[tuple[int, datetime]]) -> Path:
    ca_cert, ca_key = ca_mod.load_ca(project_dir)
    now = ca_mod.utcnow()
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + timedelta(days=CRL_NEXT_UPDATE_DAYS))
    )
    for serial, revoked_at in entries:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(serial)
            .revocation_date(revoked_at)
            .build()
        )
    crl = builder.sign(ca_key, hashes.SHA256())
    path = crl_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    return path
