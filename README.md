# TeddyCloudHelper

Interactive, menu-driven CLI toolkit to set up and manage a self-hosted
[TeddyCloud](https://github.com/toniebox-reverse-engineering/teddycloud) server —
inspired by [TeddyCloudStarter](https://github.com/Quentendo64/TeddyCloudStarter),
reimplemented from scratch in Python.

## Features

- **Setup wizard** — generates `docker-compose.yml` and nginx config for two modes:
  - *direct*: TeddyCloud publishes its own ports (simplest)
  - *nginx*: reverse proxy with raw TLS passthrough for the Toniebox on port 443
    (SNI split or a separate WebUI port) and a TLS-terminated WebUI.
    The wizard refuses to finish with an unprotected WebUI.
- **Project settings** — change single options (hostnames, ports, deployment
  mode, security) without re-running the wizard; shows the current
  configuration on entry
- **Health check ("doctor")** — containers, ports, the certificate the box
  sees on 443 (incl. SNI-collision detection), WebUI (incl. TeddyCloud's
  security-mitigation lock), DNS for the box hostname, CA-change guard,
  box certs, known boxes, Let's Encrypt expiry, image freshness, backup age.
  Also runs headless for cron: `teddycloudhelper --doctor` (exit 1 on failures)
- **Docker management** — status, start/stop/restart, logs (incl. live
  follow), image channel latest/develop, image updates, project reset;
  adopts existing installations
- **Certificates**
  - own CA + browser client certificates (mTLS) as WebUI access control,
    with revocation via CRL (`.p12` export for browser import)
  - validate & install certificates dumped from a Toniebox
  - export TeddyCloud's `ca.der` for flashing onto the box
  - **check a patched firmware image before flashing** — verifies the
    embedded CA belongs to *this* instance (an image patched by another
    instance produces nothing but silent TLS handshake failures)
  - Let's Encrypt for the WebUI hostname (certbot side-container,
    auto-renewal); existing certificates are detected from disk and never
    re-requested
- **Security** — Basic Auth (bcrypt htpasswd), browser client certificates
  and an IP allowlist, enforced by nginx. Port 80 never proxies the web GUI
  (internet scanners reaching TeddyCloud trip its security lock) while the
  box API (`/v1/`) and ACME challenges stay reachable
- **Backup / restore** — config + certificates + firmware dumps as tar.gz;
  never audio content
- **Known boxes** — table of the boxes TeddyCloud knows, straight from the API

## Installation

```sh
uv tool install git+https://github.com/ffidan61/TeddyCloudHelper
teddycloudhelper
```

Requires Python 3.11+ and Docker with the Compose v2 plugin on the host.
Pin a version with `...TeddyCloudHelper@v0.13.1` (see tags / `CHANGELOG.md`).

## Monitoring via cron

```sh
# daily at 06:00 — exit code 1 means at least one check failed
0 6 * * * teddycloudhelper --doctor --project /path/to/project || notify-somehow
```

## Hard-won TeddyCloud facts baked into the templates

- The box fetches the time over **plain HTTP** (`/v1/time`, port 80) before
  its first TLS handshake — port 80 must proxy `/v1/`, a redirect bricks the
  box boot ("Eule").
- TeddyCloud's HTTP server drops request-body bytes that arrive in the same
  burst as the headers — a proxy in front of it must stream request bodies
  (`proxy_request_buffering off`), or every upload dies with 502.
- Port 443 is an API-only listener; the web routes live on 80/8443 only.
- An unprotected WebUI reachable from the internet triggers TeddyCloud's
  security-mitigation lock, which also cuts off the boxes.
- The box trusts exactly one CA (`certs/server/ca.der`). Never regenerate it
  once boxes are flashed — the doctor guards its fingerprint.

## Development

```sh
uv sync                  # venv + all deps
uv run teddycloudhelper
uv run ruff check
uv run pytest            # no Docker required
TCH_DOCKER_TESTS=1 uv run pytest -m docker   # opt-in: validates rendered
                                             # configs with real nginx/compose
```

See `CLAUDE.md` for architecture notes.

## License

MIT
