# TeddyCloudHelper

Interactive, menu-driven CLI toolkit to set up and manage a self-hosted
[TeddyCloud](https://github.com/toniebox-reverse-engineering/teddycloud) server —
inspired by [TeddyCloudStarter](https://github.com/Quentendo64/TeddyCloudStarter),
reimplemented from scratch in Python.

## Features

- **Setup wizard** — generates `docker-compose.yml` and nginx config for two modes:
  - *direct*: TeddyCloud publishes its own ports (simplest)
  - *nginx*: reverse proxy with raw TLS passthrough for the Toniebox on port 443
    (SNI split or a separate WebUI port) and a TLS-terminated WebUI
- **Docker management** — status, start/stop/restart, logs, image updates,
  project reset (`down` incl. volumes, with backup offer and typed confirmation);
  adopts existing installations
- **Certificates**
  - own CA + browser client certificates (mTLS) as WebUI access control,
    with revocation via CRL (`.p12` export for browser import)
  - validate & install certificates dumped from a Toniebox
  - export TeddyCloud's `ca.der` for flashing onto the box
  - Let's Encrypt for the WebUI hostname (certbot side-container, auto-renewal)
- **Security** — Basic Auth (bcrypt htpasswd) and an IP allowlist, enforced by nginx
- **Backup / restore** — config + certificates as tar.gz; never audio content

## Installation

```sh
uv tool install git+https://github.com/ffidan61/TeddyCloudHelper
teddycloudhelper
```

Requires Python 3.11+ and Docker with the Compose v2 plugin on the host.

## Development

```sh
uv sync            # venv + all deps
uv run teddycloudhelper
uv run ruff check
uv run pytest      # no Docker required
```

See `CLAUDE.md` for architecture notes.

## License

MIT
