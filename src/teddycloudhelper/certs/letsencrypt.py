"""Let's Encrypt for the WebUI hostname (certbot in docker, webroot HTTP-01).

Only the nginx-terminated WebUI can use Let's Encrypt — the box path on 443
is raw TLS passthrough against TeddyCloud's own CA and can never be LE.

How it fits together:

* nginx (nginx mode) always serves ``/.well-known/acme-challenge/`` from the
  ``certbot-www`` volume, exempt from Basic Auth and the IP allowlist.
* Once ``AppState.letsencrypt_enabled`` is set, the compose file gains a
  certbot side-container that renews twice a day, and nginx reloads
  periodically to pick up renewed certs. No email is registered with the
  ACME account: Let's Encrypt no longer sends expiry notifications, so the
  address would buy nothing — expiry monitoring is this tool's job
  (:func:`renewal_warning`).
* The first certificate is issued with a one-off
  ``docker compose run --rm certbot certonly …`` (see :func:`certonly_args`);
  afterwards ``AppState.webui_tls_mode`` flips to ``"letsencrypt"`` and the
  re-rendered nginx config points at ``/etc/letsencrypt/live/<hostname>/``.
"""

from __future__ import annotations

import contextlib
import ipaddress
import secrets
import urllib.error
import urllib.request
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


def certonly_args(hostname: str) -> list[str]:
    """certbot arguments for the initial webroot issuance (idempotent)."""
    return [
        "certonly",
        "--webroot",
        "--webroot-path", "/var/www/certbot",
        "--domain", hostname,
        # LE sends no expiry mails anymore, so an account email buys nothing.
        "--register-unsafely-without-email",
        "--agree-tos",
        "--non-interactive",
        "--keep-until-expiring",
    ]


def live_cert_dir(project_dir: Path, hostname: str) -> Path:
    """Where certbot puts the issued cert (host side of the volume)."""
    return project_dir / "letsencrypt" / "live" / hostname


def cert_exists(project_dir: Path, hostname: str) -> bool:
    path = live_cert_dir(project_dir, hostname) / "fullchain.pem"
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except (PermissionError, NotADirectoryError):
        # certbot runs as root in its container and typically leaves
        # ./letsencrypt root-owned — not being allowed to look inside still
        # means issuance happened.
        return True
    return True


def cert_expiry(project_dir: Path, hostname: str) -> datetime | None:
    """Expiry of the issued leaf cert, or None if there is none (yet)."""
    path = live_cert_dir(project_dir, hostname) / "fullchain.pem"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except PermissionError:
        raise CertError(
            f"Cannot read {path} (permission denied). certbot runs as root "
            "inside its container, so ./letsencrypt on the host is usually "
            "root-owned — run this tool with sudo to check the certificate."
        ) from None
    try:
        # The first cert in the fullchain is the leaf.
        cert = x509.load_pem_x509_certificate(data)
    except ValueError as exc:
        raise CertError(f"{path} is not a valid PEM certificate: {exc}") from exc
    return cert.not_valid_after_utc


def probe_http_challenge(
    project_dir: Path, hostname: str, timeout: float = 10, port: int = 80
) -> str | None:
    """End-to-end self-test of the ACME challenge path through running nginx.

    Writes a token file into the certbot webroot and fetches it via
    ``http://hostname/.well-known/acme-challenge/…``. Returns None on
    success, otherwise a description of what failed. Runs from this host,
    so a router without hairpin NAT can fail here while Let's Encrypt
    still succeeds from outside — callers should offer to continue anyway.
    """
    challenge_dir = project_dir / "certbot-www" / ".well-known" / "acme-challenge"
    token = secrets.token_hex(16)
    probe_file = challenge_dir / f"tch-probe-{token}"
    try:
        challenge_dir.mkdir(parents=True, exist_ok=True)
        probe_file.write_text(token, encoding="utf-8")
    except OSError as exc:
        return (
            f"could not write the probe file into {challenge_dir}: {exc} "
            "(directory root-owned from an earlier docker start?)"
        )
    url = f"http://{hostname}:{port}/.well-known/acme-challenge/{probe_file.name}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(200).decode("utf-8", "replace").strip()
    except OSError as exc:  # URLError, ConnectionError, timeouts
        return f"{url} was not reachable: {exc}"
    finally:
        # Best effort — a serving process may still hold the file briefly.
        with contextlib.suppress(OSError):
            probe_file.unlink(missing_ok=True)
    if body != token:
        return (
            f"{url} answered, but not with the expected content — is a "
            "different web server listening on port 80?"
        )
    return None


def renewal_warning(project_dir: Path, state: AppState) -> str | None:
    """A human-readable warning when the LE cert is missing or expiring.

    Returns None when everything is fine (or LE is not in use). Let's
    Encrypt stopped sending expiry emails, so surfacing this in the tool is
    the only notification the user gets.
    """
    if state.webui_tls_mode != "letsencrypt":
        return None
    try:
        expiry = cert_expiry(project_dir, state.webui_hostname)
    except CertError as exc:
        # Degrade to a notice instead of hiding the problem (typically:
        # ./letsencrypt is root-owned and the tool runs unprivileged).
        return f"Could not check the Let's Encrypt certificate: {exc}"
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
