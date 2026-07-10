"""Health checks ("doctor"): verify a deployment end to end.

Each check inspects one aspect — containers, container mounts, published
ports, the TLS certificate the box sees on 443, the WebUI, DNS redirection,
box certs, Let's Encrypt — and returns a :class:`CheckResult` (ok / warn / fail with a
human-readable detail). All network and docker access goes through the
:class:`Probes` seam so tests never need Docker or open sockets.

The single most valuable check is :func:`check_box_tls`: the classic
misconfiguration in shared-443 mode is nginx routing the box path to the
WebUI certificate — the box gives no feedback at all, it just stops
connecting.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

from teddycloudhelper import backup, docker_cli, ports
from teddycloudhelper.certs import ca as ca_mod
from teddycloudhelper.certs import letsencrypt, server_certs
from teddycloudhelper.certs.ca import CertError
from teddycloudhelper.state import AppState

# The hostname the box contacts; its DNS must point at the TeddyCloud host.
BOX_HOSTNAME = "prod.de.tbs.toys"

# The original certs dumped from the box (TeddyCloud's client identity
# against the real Boxine cloud).
BOX_CERT_FILES = ("ca.der", "client.der", "private.der")

# Nag when the newest backup is older than this.
BACKUP_WARN_DAYS = 30

TEDDYCLOUD_IMAGE = "ghcr.io/toniebox-reverse-engineering/teddycloud"

# Container paths the compose template mounts into the teddycloud service.
# A stale or hand-edited compose that misses one keeps running happily but
# breaks silently later: data written into the container layer is lost on
# the next image update, and a missing library/custom_img made the WebUI
# image upload 500. test_render.py asserts the template matches this tuple,
# so neither side can drift without a red test.
TEDDYCLOUD_MOUNTS = (
    "/teddycloud/certs",
    "/teddycloud/config",
    "/teddycloud/data/content",
    "/teddycloud/data/library",
    "/teddycloud/data/www/custom_img",
    "/teddycloud/data/library/custom_img",
    "/teddycloud/data/firmware",
    "/teddycloud/data/cache",
    "/teddycloud/data/www/plugins",
)

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
    # (port, sni, host_header, path) -> (status, body head)
    http_get: Callable[[int, str | None, str, str], tuple[int, str]]
    resolve: Callable[[str], list[str]]
    # Raw JSON of /api/getBoxes, queried inside the container so neither
    # nginx routing nor WebUI auth can get in the way.
    getboxes: Callable[[], str]
    # sha256 digest of an image ref, locally and in the registry (None if
    # undeterminable — docker missing, registry unreachable, never pulled).
    local_image_digest: Callable[[str], str | None]
    remote_image_digest: Callable[[str], str | None]
    # Live mounts of the teddycloud container: destination -> host source.
    # Raises DockerError when the container does not exist (yet).
    teddycloud_mounts: Callable[[], dict[str, str]]
    # Human-readable version: the running image's OCI version label and the
    # newest TeddyCloud GitHub release (None if undeterminable). Purely
    # informational — the digest comparison above is the source of truth.
    local_image_version: Callable[[str], str | None] = lambda image: None
    latest_teddycloud_release: Callable[[], str | None] = lambda: None


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


def _http_get(
    port: int, server_name: str | None, host_header: str, path: str = "/"
) -> tuple[int, str]:
    """Status code + body head of a ``GET`` over TLS. Raw socket instead of
    urllib so the SNI (routing) and the Host header can differ from the
    connect address."""
    request = f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\n\r\n"
    with (
        socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT) as raw,
        _tls_context().wrap_socket(raw, server_hostname=server_name) as tls,
    ):
        tls.sendall(request.encode("ascii"))
        data = b""
        while len(data) < 8192:
            chunk = tls.recv(1024)
            if not chunk:
                break
            data += chunk
    try:
        status = int(data.split(b"\r\n", 1)[0].split()[1])
    except (IndexError, ValueError):
        raise OSError(f"no HTTP status line in response: {data[:80]!r}") from None
    body = data.partition(b"\r\n\r\n")[2]
    return status, body[:2048].decode("utf-8", "replace")


def _resolve(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None)
    return sorted({info[4][0] for info in infos})


def _local_image_digest(image: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out.rsplit("@", 1)[-1] if "@" in out else None


def _remote_image_digest(image: str) -> str | None:
    """Manifest digest from the ghcr registry (anonymous pull token)."""
    name, _, tag = image.rpartition(":")
    name = name.removeprefix("ghcr.io/")
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?service=ghcr.io&scope=repository:{name}:pull",
            timeout=_PROBE_TIMEOUT,
        ) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{name}/manifests/{tag}",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {token}",
                # Multi-arch images: ask for the index, whose digest is what
                # `docker pull` records in RepoDigests.
                "Accept": "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json",
            },
        )
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:
            return response.headers.get("Docker-Content-Digest")
    except (OSError, ValueError, KeyError):
        return None


def _local_image_version(image: str) -> str | None:
    """The running image's ``org.opencontainers.image.version`` label."""
    try:
        result = subprocess.run(
            [
                "docker", "image", "inspect", "--format",
                '{{index .Config.Labels "org.opencontainers.image.version"}}',
                image,
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _latest_teddycloud_release() -> str | None:
    """The newest TeddyCloud GitHub release tag (anonymous, best-effort)."""
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/toniebox-reverse-engineering/"
            "teddycloud/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "TeddyCloudHelper"},
        )
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:
            return json.load(response).get("tag_name") or None
    except (OSError, ValueError):
        return None


def default_probes(project_dir: Path) -> Probes:
    compose = docker_cli.Compose(project_dir)

    def getboxes() -> str:
        # curl is installed in the teddycloud image; querying inside the
        # container bypasses nginx routing and any WebUI auth.
        return compose.exec_service(
            "teddycloud", "curl", "-s", "http://localhost:80/api/getBoxes"
        ).stdout

    def teddycloud_mounts() -> dict[str, str]:
        # Resolve the container name via compose ps — adopted installs may
        # not use the template's fixed container_name.
        names = [svc.name for svc in compose.ps() if svc.service == "teddycloud"]
        if not names or not names[0]:
            raise docker_cli.DockerError("no teddycloud container exists yet")
        try:
            result = subprocess.run(
                ["docker", "container", "inspect", "--format", "{{json .Mounts}}", names[0]],
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise docker_cli.DockerError(f"Could not run 'docker': {exc}") from exc
        if result.returncode != 0:
            raise docker_cli.DockerError(
                result.stderr.strip() or "docker container inspect failed"
            )
        try:
            mounts = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise docker_cli.DockerError(f"Unparseable inspect output: {exc}") from exc
        return {m["Destination"]: m.get("Source", "") for m in mounts}

    return Probes(
        ps=compose.ps,
        listening=_listening,
        tls_cert=_tls_cert,
        http_get=_http_get,
        resolve=_resolve,
        getboxes=getboxes,
        local_image_digest=_local_image_digest,
        remote_image_digest=_remote_image_digest,
        teddycloud_mounts=teddycloud_mounts,
        local_image_version=_local_image_version,
        latest_teddycloud_release=_latest_teddycloud_release,
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
    unhealthy = [svc for svc in services if svc.health == "unhealthy"]
    if unhealthy:
        listing = ", ".join(svc.service for svc in unhealthy)
        return CheckResult(
            "Containers",
            "fail",
            f"Running but unhealthy (failing its healthcheck): {listing} — "
            "check its logs.",
        )
    starting = [svc for svc in services if svc.health == "starting"]
    if starting:
        listing = ", ".join(svc.service for svc in starting)
        return CheckResult(
            "Containers",
            "warn",
            f"Still starting (healthcheck not green yet): {listing} — "
            "first-start certificate generation can take minutes.",
        )
    return CheckResult("Containers", "ok", f"All {len(services)} container(s) running.")


def check_mounts(probes: Probes) -> CheckResult:
    """The running container's mounts vs. what the current template expects.

    Catches stale deployments: a compose rendered before the template gained
    a mount keeps running fine until the feature needing it breaks — e.g. the
    WebUI image upload 500ing because library/custom_img was never mounted.
    """
    name = "Container mounts"
    try:
        mounts = probes.teddycloud_mounts()
    except docker_cli.DockerError as exc:
        return CheckResult(
            name, "warn", f"Could not inspect the teddycloud container: {exc}"
        )
    missing = [dest for dest in TEDDYCLOUD_MOUNTS if dest not in mounts]
    if missing:
        return CheckResult(
            name,
            "fail",
            f"Missing mount(s): {', '.join(missing)} — the compose file "
            "predates the current template (or was hand-edited). Use "
            "'Re-render config files' under Project settings, then restart.",
        )
    if (
        mounts["/teddycloud/data/www/custom_img"]
        != mounts["/teddycloud/data/library/custom_img"]
    ):
        return CheckResult(
            name,
            "fail",
            "www/custom_img and library/custom_img are mounted from different "
            "host directories — WebUI image uploads land outside the served "
            "directory and never show up. Use 'Re-render config files' under "
            "Project settings, then restart.",
        )
    return CheckResult(
        name, "ok", f"All {len(TEDDYCLOUD_MOUNTS)} expected mounts are present."
    )


def check_ports(state: AppState, probes: Probes) -> CheckResult:
    silent = [port for port in ports.required_ports(state) if not probes.listening(port)]
    if silent:
        listing = ", ".join(str(p) for p in silent)
        return CheckResult(
            "Ports", "fail", f"Nothing listens on port(s) {listing} on this host."
        )
    return CheckResult("Ports", "ok", "Something listens on every published port.")


def _classify_box_path_cert(
    state: AppState, cert: x509.Certificate, name: str, via: str
) -> CheckResult:
    """Shared verdict: is this the cert a box must see on 443?"""
    subject, issuer = _cn(cert.subject), _cn(cert.issuer)
    if "let's encrypt" in cert.issuer.rfc4514_string().lower():
        return CheckResult(
            name,
            "fail",
            f"{via} presents a Let's Encrypt certificate ({subject}) — the "
            "box path is being TLS-terminated instead of passed through. "
            "Boxes cannot connect; re-run the setup wizard.",
        )
    if state.webui_hostname and subject == state.webui_hostname:
        return CheckResult(
            name,
            "fail",
            f"{via} presents the WebUI certificate ({subject}) — the SNI "
            "split routes box traffic to the WebUI. Boxes cannot connect.",
        )
    return CheckResult(
        name, "ok", f"Presents {subject!r} (issuer {issuer!r}) — TeddyCloud's own cert."
    )


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
    return _classify_box_path_cert(state, cert, name, "Port 443")


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
        status, body = probes.http_get(port, sni, sni or "localhost", "/")
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
    # TeddyCloud's security mitigation answers with these texts (and cuts
    # off the boxes too) once internet scanners have reached it.
    if "locked to mitigate security risks" in body:
        return CheckResult(
            name,
            "fail",
            "TeddyCloud has LOCKED itself (security mitigation) — the boxes "
            "are cut off too. Enable Basic Auth, client certificates or the "
            "IP allowlist, then restart the services to clear the lock.",
        )
    if "detected security risks" in body:
        return CheckResult(
            name,
            "warn",
            "TeddyCloud reports detected security risks — check the "
            "teddycloud logs and protect the WebUI before it locks itself.",
        )
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
    """DNS for the original Boxine name: DNS-redirect setups mean the box's
    view of this name can differ from this machine's."""
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


def check_letsencrypt(project_dir: Path, state: AppState, probes: Probes) -> CheckResult:
    name = "Let's Encrypt"
    source = ""
    try:
        expiry = letsencrypt.cert_expiry(project_dir, state.webui_hostname)
    except CertError:
        # ./letsencrypt is typically root-owned (certbot runs as root in its
        # container) — read the certificate nginx actually serves instead.
        port, sni = _webui_endpoint(state)
        try:
            expiry = probes.tls_cert(port, sni).not_valid_after_utc
        except OSError as exc:
            return CheckResult(
                name,
                "warn",
                "Cannot read the certificate file (root-owned ./letsencrypt?) "
                f"and probing the live WebUI certificate failed too: {exc}",
            )
        source = " (read from the live WebUI certificate)"
    if expiry is None:
        return CheckResult(
            name,
            "warn",
            "Let's Encrypt mode is active but no certificate was found — "
            "re-run the Let's Encrypt setup from the certificate menu.",
        )
    days = (expiry - ca_mod.utcnow()).days
    if days < 0:
        return CheckResult(
            name,
            "fail",
            f"The certificate for {state.webui_hostname} EXPIRED {-days} "
            "day(s) ago — automatic renewal is broken; check "
            "`docker compose logs certbot`.",
        )
    if days <= letsencrypt.RENEWAL_WARN_DAYS:
        return CheckResult(
            name,
            "warn",
            f"The certificate for {state.webui_hostname} expires in {days} "
            "day(s); certbot renews at 30 days remaining, so automatic "
            "renewal seems to be failing.",
        )
    return CheckResult(
        name,
        "ok",
        f"Certificate for {state.webui_hostname} valid until "
        f"{expiry:%Y-%m-%d}{source}.",
    )


def check_image_freshness(state: AppState, probes: Probes) -> CheckResult:
    """Is the running TeddyCloud image the newest one on its channel?"""
    name = "TeddyCloud image freshness"
    image = f"{TEDDYCLOUD_IMAGE}:{state.teddycloud_image_tag}"
    local = probes.local_image_digest(image)
    if local is None:
        return CheckResult(
            name, "warn", f"No local digest for {image} — image never pulled?"
        )
    remote = probes.remote_image_digest(image)
    if remote is None:
        return CheckResult(
            name, "warn", "Could not query the registry (offline?) — skipping."
        )
    version_note = _teddycloud_version_note(state, probes, image)
    if local != remote:
        return CheckResult(
            name,
            "warn",
            f"A newer {state.teddycloud_image_tag!r} image is available — "
            "use 'Pull latest images' in the Docker menu." + version_note,
        )
    return CheckResult(
        name,
        "ok",
        f"Running the newest {state.teddycloud_image_tag!r} image." + version_note,
    )


def _teddycloud_version_note(state: AppState, probes: Probes, image: str) -> str:
    """Human-readable running-vs-latest version, appended to the detail.

    Empty when nothing is known. The GitHub-release comparison only makes
    sense on the ``latest`` channel — a ``develop`` build has no matching
    release tag, so the digest comparison above stays the sole measure there.
    """
    running = probes.local_image_version(image)
    if state.teddycloud_image_tag != "latest":
        return f" Running {running}." if running else ""
    latest = probes.latest_teddycloud_release()
    if running and latest:
        if running != latest:
            return f" Running {running}; latest release {latest}."
        return f" Running {running} (latest release)."
    if running:
        return f" Running {running}."
    if latest:
        return f" Latest release {latest}."
    return ""


def check_ca_identity(project_dir: Path, state: AppState) -> CheckResult:
    """The box CA must never change silently — flashed boxes trust exactly
    this CA. Records the fingerprint on first sight (caller saves state)."""
    name = "Server CA"
    fingerprint = server_certs.box_ca_fingerprint(project_dir)
    if fingerprint is None:
        return CheckResult(
            name,
            "warn",
            "certs/server/ca.der does not exist yet — TeddyCloud generates "
            "it on first container start.",
        )
    ca = server_certs.load_box_ca(project_dir)
    identity = f"serial {ca.serial_number:x}, SHA-256 {fingerprint[:16]}…"
    if not state.known_ca_fingerprint:
        state.known_ca_fingerprint = fingerprint
        return CheckResult(name, "ok", f"Recorded the box CA ({identity}).")
    if state.known_ca_fingerprint != fingerprint:
        return CheckResult(
            name,
            "fail",
            f"The box CA CHANGED since it was last recorded ({identity}) — "
            "every box flashed against the old CA now fails its TLS "
            "handshake. Restore certs/server/ from a backup, or re-flash "
            "the boxes and accept the new CA when asked.",
        )
    return CheckResult(name, "ok", f"Unchanged ({identity}).")


def check_backup(project_dir: Path) -> CheckResult:
    """certs/ (box CA + dumped box certs) is irreplaceable — remind."""
    name = "Backup"
    backups = backup.list_backups(project_dir)
    if not backups:
        return CheckResult(
            name,
            "warn",
            "No backup exists yet — certs/ (box CA + dumped box certs) is "
            "irreplaceable. Create one via the backup menu.",
        )
    age_days = (
        ca_mod.utcnow()
        - datetime.fromtimestamp(backups[0].stat().st_mtime, tz=UTC)
    ).days
    if age_days > BACKUP_WARN_DAYS:
        return CheckResult(
            name,
            "warn",
            f"The latest backup is {age_days} day(s) old — create a fresh "
            "one via the backup menu.",
        )
    return CheckResult(
        name, "ok", f"Latest backup is {age_days} day(s) old ({backups[0].name})."
    )


def check_boxes(probes: Probes) -> CheckResult:
    """Which boxes TeddyCloud knows — the end-to-end proof a box connected."""
    name = "Known boxes"
    try:
        raw = probes.getboxes()
    except docker_cli.DockerError as exc:
        return CheckResult(name, "warn", f"Could not query /api/getBoxes: {exc}")
    try:
        boxes = json.loads(raw)["boxes"]
    except (ValueError, KeyError, TypeError):
        return CheckResult(
            name, "warn", f"Unexpected /api/getBoxes answer: {raw[:120]!r}"
        )
    if not boxes:
        return CheckResult(
            name,
            "warn",
            "TeddyCloud knows no boxes yet — no box has ever connected (or "
            "been configured). Check DNS/CA flashing if one should have.",
        )
    listing = ", ".join(
        f"{box.get('boxName') or '?'} ({box.get('commonName') or box.get('ID', '?')})"
        for box in boxes
    )
    return CheckResult(name, "ok", f"{len(boxes)} box(es): {listing}.")


def check_webui_protection(state: AppState) -> CheckResult:
    """An unprotected WebUI trips TeddyCloud's security-mitigation lock as
    soon as internet scanners find it — which also cuts off the boxes."""
    name = "WebUI protection"
    active = []
    if state.basic_auth_enabled:
        active.append("Basic Auth")
    if state.webui_client_cert_auth:
        active.append("client certificates")
    if state.ip_allowlist:
        active.append("IP allowlist")
    if active:
        return CheckResult(name, "ok", f"Protected by: {', '.join(active)}.")
    return CheckResult(
        name,
        "warn",
        "No Basic Auth, client certificates or IP allowlist — if the WebUI "
        "is reachable from the internet, TeddyCloud locks itself once "
        "scanners find it (and the boxes stop working). Enable one of the "
        "three under Project settings / Security.",
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
        check_mounts(probes),
        check_ports(state, probes),
        check_box_tls(state, probes),
        check_webui(state, probes),
        check_webui_protection(state),
        check_box_dns(probes),
        check_ca_identity(project_dir, state),
        check_box_certs(project_dir),
        check_boxes(probes),
        check_image_freshness(state, probes),
    ]
    if state.webui_tls_mode == "letsencrypt":
        results.append(check_letsencrypt(project_dir, state, probes))
    results.append(check_backup(project_dir))
    return results
