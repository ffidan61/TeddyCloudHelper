"""Health checks ("doctor"): verify a deployment end to end.

Each check inspects one aspect — containers, published ports, the TLS
certificate the box sees on 443, the WebUI, DNS redirection, box certs,
Let's Encrypt — and returns a :class:`CheckResult` (ok / warn / fail with a
human-readable detail). All network and docker access goes through the
:class:`Probes` seam so tests never need Docker or open sockets.

The single most valuable check is :func:`check_box_tls`: the classic
misconfiguration in shared-443 mode is nginx routing the box path to the
WebUI certificate — the box gives no feedback at all, it just stops
connecting.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

from teddycloudhelper import docker_cli, ports
from teddycloudhelper.certs import letsencrypt
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.state import AppState

# The hostname the box contacts; its DNS must point at the TeddyCloud host.
BOX_HOSTNAME = "prod.de.tbs.toys"

# The original certs dumped from the box (TeddyCloud's client identity
# against the real Boxine cloud).
BOX_CERT_FILES = ("ca.der", "client.der", "private.der")

_PROBE_TIMEOUT = 5.0


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str


@dataclass
class Probes:
    """Injectable I/O for the checks (see module docstring)."""

    ps: Callable[[], list[docker_cli.ServiceStatus]]
    listening: Callable[[int], bool]
    tls_cert: Callable[[int, str | None], x509.Certificate]
    http_get: Callable[[int, str | None, str], int]
    resolve: Callable[[str], list[str]]


# --- default (real) probe implementations ------------------------------------


def _listening(port: int) -> bool:
    return bool(ports.check_ports([port]))


def _tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # we inspect, we don't trust
    return context


def _tls_cert(port: int, server_name: str | None) -> x509.Certificate:
    with (
        socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT) as raw,
        _tls_context().wrap_socket(raw, server_hostname=server_name) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if der is None:  # pragma: no cover - TLS always has a server cert
        raise ssl.SSLError("no peer certificate received")
    return x509.load_der_x509_certificate(der)


def _http_get(port: int, server_name: str | None, host_header: str) -> int:
    """Status code of ``GET /`` over TLS. Raw socket instead of urllib so the
    SNI (routing) and the Host header can differ from the connect address."""
    request = f"GET / HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\n\r\n"
    with (
        socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT) as raw,
        _tls_context().wrap_socket(raw, server_hostname=server_name) as tls,
    ):
        tls.sendall(request.encode("ascii"))
        data = b""
        while b"\r\n" not in data and len(data) < 1024:
            chunk = tls.recv(256)
            if not chunk:
                break
            data += chunk
    try:
        return int(data.split(b"\r\n", 1)[0].split()[1])
    except (IndexError, ValueError):
        raise OSError(f"no HTTP status line in response: {data[:80]!r}") from None


def _resolve(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None)
    return sorted({info[4][0] for info in infos})


def default_probes(project_dir: Path) -> Probes:
    return Probes(
        ps=docker_cli.Compose(project_dir).ps,
        listening=_listening,
        tls_cert=_tls_cert,
        http_get=_http_get,
        resolve=_resolve,
    )


# --- checks -------------------------------------------------------------------


def _cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else name.rfc4514_string()


def check_containers(probes: Probes) -> CheckResult:
    try:
        services = probes.ps()
    except docker_cli.DockerError as exc:
        return CheckResult("Containers", "fail", str(exc))
    if not services:
        return CheckResult(
            "Containers", "warn", "No containers exist yet — start the services first."
        )
    stopped = [svc for svc in services if svc.state != "running"]
    if stopped:
        listing = ", ".join(f"{svc.service} ({svc.state})" for svc in stopped)
        return CheckResult("Containers", "fail", f"Not running: {listing}.")
    return CheckResult("Containers", "ok", f"All {len(services)} container(s) running.")


def check_ports(state: AppState, probes: Probes) -> CheckResult:
    silent = [port for port in ports.required_ports(state) if not probes.listening(port)]
    if silent:
        listing = ", ".join(str(p) for p in silent)
        return CheckResult(
            "Ports", "fail", f"Nothing listens on port(s) {listing} on this host."
        )
    return CheckResult("Ports", "ok", "Something listens on every published port.")


def check_box_tls(state: AppState, probes: Probes) -> CheckResult:
    """The certificate on 443 as the box sees it (no SNI, like a Toniebox)."""
    name = "Box port 443"
    try:
        cert = probes.tls_cert(443, None)
    except ssl.SSLError as exc:
        return CheckResult(
            name,
            "warn",
            "TLS answered but the handshake did not complete "
            f"({exc}) — TeddyCloud may simply require the box client "
            "certificate, which this probe does not have.",
        )
    except OSError as exc:
        return CheckResult(name, "fail", f"No TLS on 127.0.0.1:443: {exc}")
    subject, issuer = _cn(cert.subject), _cn(cert.issuer)
    issuer_full = cert.issuer.rfc4514_string()
    if "let's encrypt" in issuer_full.lower():
        return CheckResult(
            name,
            "fail",
            f"Port 443 presents a Let's Encrypt certificate ({subject}) — the "
            "box path is being TLS-terminated instead of passed through. "
            "Boxes cannot connect; re-run the setup wizard.",
        )
    if state.webui_hostname and subject == state.webui_hostname:
        return CheckResult(
            name,
            "fail",
            f"Port 443 presents the WebUI certificate ({subject}) — the SNI "
            "split routes box traffic to the WebUI. Boxes cannot connect.",
        )
    return CheckResult(
        name, "ok", f"Presents {subject!r} (issuer {issuer!r}) — TeddyCloud's own cert."
    )


def _webui_endpoint(state: AppState) -> tuple[int, str | None]:
    """(host port, SNI) the WebUI should answer on, per mode."""
    if state.deployment_mode == "direct":
        return state.webui_port, None
    if state.webui_port_mode == "shared":
        return 443, state.webui_hostname
    return state.webui_port, state.webui_hostname


def check_webui(state: AppState, probes: Probes) -> CheckResult:
    port, sni = _webui_endpoint(state)
    name = "WebUI"
    where = f"127.0.0.1:{port}" + (f" (SNI {sni})" if sni else "")
    try:
        status = probes.http_get(port, sni, sni or "localhost")
    except ssl.SSLError as exc:
        if state.webui_client_cert_auth:
            return CheckResult(
                name,
                "ok",
                "TLS handshake rejected without a client certificate — "
                "client-cert auth is enforced.",
            )
        return CheckResult(name, "fail", f"TLS handshake with {where} failed: {exc}")
    except OSError as exc:
        return CheckResult(name, "fail", f"{where} is not reachable: {exc}")
    if status == 401 and state.basic_auth_enabled:
        return CheckResult(name, "ok", f"Answers on {where}; Basic Auth is enforced (401).")
    if status == 400 and state.webui_client_cert_auth:
        return CheckResult(
            name,
            "ok",
            f"Answers on {where}; client certificates are enforced "
            "(400 without one).",
        )
    if status == 502:
        return CheckResult(
            name,
            "warn",
            f"{where} answers 502 — nginx is up but TeddyCloud is not "
            "responding (first-start certificate generation can take "
            "minutes; check the teddycloud logs).",
        )
    if status == 403:
        return CheckResult(
            name,
            "warn",
            f"{where} answers 403 — the IP allowlist may not cover this machine.",
        )
    if 200 <= status < 400:
        return CheckResult(name, "ok", f"Answers on {where} (HTTP {status}).")
    return CheckResult(name, "warn", f"{where} answers HTTP {status}.")


def check_box_dns(probes: Probes) -> CheckResult:
    """Where BOX_HOSTNAME points *from this machine* — the box's view can
    differ (per-device DNS override), so failures are warnings, not errors."""
    name = f"DNS {BOX_HOSTNAME}"
    try:
        addresses = probes.resolve(BOX_HOSTNAME)
    except OSError:
        return CheckResult(
            name,
            "warn",
            f"{BOX_HOSTNAME} does not resolve from this machine. Fine if only "
            "the box's DNS is redirected — but the BOX must resolve it to the "
            "TeddyCloud host.",
        )
    public = [a for a in addresses if ipaddress.ip_address(a).is_global]
    if public:
        return CheckResult(
            name,
            "warn",
            f"Resolves to public IP(s) {', '.join(public)} — from here the "
            "name still points at the real Boxine cloud. Make sure the BOX "
            "uses the redirected DNS.",
        )
    return CheckResult(
        name,
        "ok",
        f"Resolves to {', '.join(addresses)} — make sure that is the "
        "TeddyCloud host.",
    )


def check_box_certs(project_dir: Path) -> CheckResult:
    client_dir = project_dir / "certs" / "client"
    missing = [f for f in BOX_CERT_FILES if not (client_dir / f).is_file()]
    if not missing:
        return CheckResult(
            "Box certificates", "ok", f"Dumped box certs present in {client_dir}."
        )
    if len(missing) == len(BOX_CERT_FILES):
        return CheckResult(
            "Box certificates",
            "warn",
            "No dumped box certs installed — TeddyCloud cannot fetch "
            "original Boxine content (fine for library-only use).",
        )
    return CheckResult(
        "Box certificates",
        "fail",
        f"Incomplete: {', '.join(missing)} missing from {client_dir} — "
        "re-install the dumped certs via the certificate menu.",
    )


def check_letsencrypt(project_dir: Path, state: AppState) -> CheckResult:
    name = "Let's Encrypt"
    warning = letsencrypt.renewal_warning(project_dir, state)
    if warning:
        return CheckResult(name, "warn", warning)
    try:
        expiry = letsencrypt.cert_expiry(project_dir, state.webui_hostname)
    except CertError as exc:
        return CheckResult(name, "warn", str(exc))
    if expiry is None:  # pragma: no cover - renewal_warning already caught this
        return CheckResult(name, "warn", "No certificate found.")
    return CheckResult(
        name, "ok", f"Certificate for {state.webui_hostname} valid until {expiry:%Y-%m-%d}."
    )


def check_files(project_dir: Path, state: AppState) -> CheckResult:
    missing = []
    if docker_cli.find_compose_file(project_dir) is None:
        missing.append("a compose file")
    if state.deployment_mode == "nginx" and not (
        project_dir / "nginx" / "nginx.conf"
    ).is_file():
        missing.append("nginx/nginx.conf")
    if missing:
        return CheckResult(
            "Config files",
            "fail",
            f"Missing in {project_dir}: {', '.join(missing)} — re-run the setup wizard.",
        )
    return CheckResult("Config files", "ok", "Compose and nginx configs are in place.")


def run_checks(
    project_dir: Path, state: AppState, probes: Probes | None = None
) -> list[CheckResult]:
    """All checks that apply to this project's configuration."""
    probes = probes or default_probes(project_dir)
    results = [
        check_files(project_dir, state),
        check_containers(probes),
        check_ports(state, probes),
        check_box_tls(state, probes),
        check_webui(state, probes),
        check_box_dns(probes),
        check_box_certs(project_dir),
    ]
    if state.webui_tls_mode == "letsencrypt":
        results.append(check_letsencrypt(project_dir, state))
    return results
