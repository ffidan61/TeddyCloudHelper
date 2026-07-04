"""Let's Encrypt for the WebUI hostname (certbot in docker, webroot HTTP-01).

Only the nginx-terminated WebUI can use Let's Encrypt — the box path on 443
is raw TLS passthrough against TeddyCloud's own CA and can never be LE.

How it fits together:

* nginx (nginx mode) always serves ``/.well-known/acme-challenge/`` from the
  ``certbot-www`` volume, exempt from Basic Auth and the IP allowlist.
* Once ``AppState.letsencrypt_email`` is set, the compose file gains a
  certbot side-container that renews twice a day, and nginx reloads
  periodically to pick up renewed certs.
* The first certificate is issued with a one-off
  ``docker compose run --rm certbot certonly …`` (see :func:`certonly_args`);
  afterwards ``AppState.webui_tls_mode`` flips to ``"letsencrypt"`` and the
  re-rendered nginx config points at ``/etc/letsencrypt/live/<hostname>/``.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from pathlib import Path

from cryptography import x509

from teddycloudhelper.certs import ca as ca_mod
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.state import AppState

# certbot renews once fewer than 30 days remain; if we ever see fewer than
# this, renewal has been failing for over a week. Let's Encrypt no longer
# sends expiry notification emails, so this tool has to do the warning.
RENEWAL_WARN_DAYS = 21

# Names that public CAs will never issue for.
_BLOCKED_SUFFIXES = (".local", ".localhost", ".lan", ".home", ".internal", ".arpa")


def validate_hostname(hostname: str) -> str:
    """Check that *hostname* can plausibly get a public certificate."""
    hostname = hostname.strip().lower()
    if not hostname:
        raise CertError("No WebUI hostname configured — run the setup wizard first.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise CertError(
            f"{hostname!r} is an IP address — Let's Encrypt only issues "
            "certificates for public DNS names."
        )
    if "*" in hostname:
        raise CertError("Wildcard hostnames need DNS-01, which this tool does not do.")
    if "." not in hostname or hostname.endswith(_BLOCKED_SUFFIXES):
        raise CertError(
            f"{hostname!r} is not a public DNS name — Let's Encrypt cannot "
            "issue for it. Use the self-signed WebUI certificate instead."
        )
    return hostname


def validate_email(email: str) -> str:
    email = email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise CertError(f"{email!r} does not look like an email address.")
    return email


def certonly_args(hostname: str, email: str) -> list[str]:
    """certbot arguments for the initial webroot issuance (idempotent)."""
    return [
        "certonly",
        "--webroot",
        "--webroot-path", "/var/www/certbot",
        "--domain", hostname,
        "--email", email,
        "--agree-tos",
        "--no-eff-email",
        "--non-interactive",
        "--keep-until-expiring",
    ]


def live_cert_dir(project_dir: Path, hostname: str) -> Path:
    """Where certbot puts the issued cert (host side of the volume)."""
    return project_dir / "letsencrypt" / "live" / hostname


def cert_exists(project_dir: Path, hostname: str) -> bool:
    return (live_cert_dir(project_dir, hostname) / "fullchain.pem").is_file()


def cert_expiry(project_dir: Path, hostname: str) -> datetime | None:
    """Expiry of the issued leaf cert, or None if there is none (yet)."""
    path = live_cert_dir(project_dir, hostname) / "fullchain.pem"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        # The first cert in the fullchain is the leaf.
        cert = x509.load_pem_x509_certificate(data)
    except ValueError as exc:
        raise CertError(f"{path} is not a valid PEM certificate: {exc}") from exc
    return cert.not_valid_after_utc


def renewal_warning(project_dir: Path, state: AppState) -> str | None:
    """A human-readable warning when the LE cert is missing or expiring.

    Returns None when everything is fine (or LE is not in use). Let's
    Encrypt stopped sending expiry emails, so surfacing this in the tool is
    the only notification the user gets.
    """
    if state.webui_tls_mode != "letsencrypt":
        return None
    expiry = cert_expiry(project_dir, state.webui_hostname)
    if expiry is None:
        return (
            "Let's Encrypt mode is active but no certificate was found in "
            f"{live_cert_dir(project_dir, state.webui_hostname)} — nginx will "
            "fail to start. Re-run the Let's Encrypt setup from the "
            "certificate menu."
        )
    days = (expiry - ca_mod.utcnow()).days
    if days < 0:
        return (
            f"The Let's Encrypt certificate for {state.webui_hostname} "
            f"EXPIRED {-days} day(s) ago! Automatic renewal is broken — "
            "check `docker compose logs certbot` and that port 80 is "
            "reachable from the internet, then use 'Renew Let's Encrypt "
            "certificate now' in the certificate menu."
        )
    if days <= RENEWAL_WARN_DAYS:
        return (
            f"The Let's Encrypt certificate for {state.webui_hostname} "
            f"expires in {days} day(s). certbot renews at 30 days remaining, "
            "so automatic renewal seems to be failing — check "
            "`docker compose logs certbot` and that port 80 is reachable "
            "from the internet."
        )
    return None
