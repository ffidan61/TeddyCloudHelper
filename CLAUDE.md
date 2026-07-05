# CLAUDE.md

TeddyCloudHelper is an interactive, menu-driven CLI toolkit to set up and manage a
self-hosted [TeddyCloud](https://github.com/toniebox-reverse-engineering/teddycloud)
server — inspired by [TeddyCloudStarter](https://github.com/Quentendo64/TeddyCloudStarter),
but a from-scratch reimplementation.

## Working in this repo

Managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + committed `uv.lock`, src/ layout).

```sh
uv sync                       # create venv + install all deps (incl. dev group)
uv run teddycloudhelper       # run the interactive CLI
uv run ruff check             # lint (config in pyproject.toml)
uv run pytest                 # run all tests
uv run pytest tests/test_state.py::test_save_then_load_roundtrip   # single test
```

- English only — no i18n, no gettext.
- Tests must not require Docker. Docker integration tests (future) go behind
  `@pytest.mark.docker` and `TCH_DOCKER_TESTS=1`.

## TeddyCloud facts (researched; re-verify against the target version before v0.3/v0.4)

- Ports: **80** (web GUI, HTTP), **443** (Toniebox endpoint, TLS), **8443** (web GUI, HTTPS).
- **Port 443 is not remappable for the box.** The Toniebox authenticates with a client
  certificate directly against TeddyCloud (mTLS), so a reverse proxy in front of it must
  do raw **TLS passthrough** (nginx `stream` + `ssl_preread` for SNI routing) — never
  TLS termination on the box path.
- Certificate layout on the TeddyCloud side: `certs/server/` and `certs/client/`.
  Server certs are auto-generated on first container start; extract
  `certs/server/ca.der` afterwards to flash onto the box.
- `certs/client/` (`ca.der` / `client.der` / `private.der`) holds the **original certs
  dumped from the box** — TeddyCloud uses them as a *client* to authenticate against the
  real Boxine cloud (fetching original content). They are irreplaceable; back them up.
- Connecting a box: dump its original certs → flash the replacement CA → redirect DNS
  (`prod.de.tbs.toys` etc.) to the TeddyCloud host.
- **TeddyCloud has no CRL support** (verified 2026-07: "crl" appears only in its vendored
  libs — CycloneSSL, FatFs — never in TeddyCloud's own code). Box client-cert validation
  is purely TLS-level; boxes are identified by the cert CN (the MAC address). Therefore
  all revocation UX targets **nginx** (`ssl_crl`), never TeddyCloud itself.
- **TeddyCloud's HTTP server drops request-body bytes that arrive in the same burst as
  the headers** (verified 2026-07 with a curl matrix: with `Expect: 100-continue` a 16 MB
  upload finishes in 1.6s; without it — any HTTP version — the server stalls ~120s and
  closes without a response). Any proxy in front of it MUST stream request bodies
  (`proxy_request_buffering off`), never buffer them.
- TeddyCloud serves web routes on ports 80/8443 only; **port 443 is an API-only listener**
  (`api_access_only`) that answers all WebUI/API-web routes with 404 "File not found".

## Architecture

Key decisions (confirmed with the maintainer):

| Area | Decision |
|---|---|
| UI | `questionary` prompts + `rich` output; all prompts via `ui.py` (Ctrl-C → `ui.Cancelled`) |
| Docker | `docker compose` CLI via subprocess (args list, never `shell=True`); local host only |
| Certificates | `cryptography` library (no openssl shell-out); htpasswd via `bcrypt` directly |
| Templates | Jinja2 for docker-compose.yml + nginx confs; render-to-file writes timestamped `.bak` first |
| State | `AppState` dataclass → `<project>/teddycloudhelper.json` with `schema_version` + migrations (`state.py`); global "last project" pointer via `platformdirs` |
| WebUI access | own CA + browser client certs (mTLS), enforced by **nginx** on the WebUI port (`ssl_client_certificate` + `ssl_verify_client on`, revocation via `ssl_crl`) — requires nginx deployment mode; TeddyCloud itself cannot do UI client-cert auth. Client certs are exported as PKCS#12 (`.p12`) for browser import. Basic Auth / IP allowlist (v0.5) as alternatives |
| Let's Encrypt | **WebUI hostname only** (v0.6): certbot side-container, webroot HTTP-01 via an ACME location on port 80 that bypasses Basic Auth/allowlist; `AppState.webui_tls_mode` switches nginx between self-signed and LE cert paths. Box traffic stays SNI passthrough — LE is impossible there |

Rules:

- Only `state.py` persists tool state. Derived state (container status, cert lists) is
  always read live, never stored.
- Every config change follows the same cycle: update state → re-render templates
  (with `.bak`) → prompt for restart.
- Menu actions must be exception-safe: errors render as a red panel and return to the
  menu; the loop never crashes.

### Module map (target)

```
src/teddycloudhelper/
├── cli.py         # main(): preflight (docker/compose available), main menu loop
├── ui.py          # rich Console + questionary wrappers (menu/confirm/ask_text/ask_path)
├── state.py       # AppState, load/save/migrations, last-project pointer
├── docker_cli.py  # (v0.2) Compose wrapper, injectable subprocess runner as test seam
├── ports.py       # host-port availability checks (connect test) before starting containers
├── certs/         # (v0.3) ca.py (own CA for WebUI access), client_certs.py (issue/renew
│                  #        browser certs, PKCS#12 export), crl.py (CRL for nginx ssl_crl),
│                  #        server_certs.py (WebUI cert, ca.der extraction),
│                  #        box_certs.py (validate + install dumped box certs),
│                  #        letsencrypt.py (v0.6: hostname/email checks, certbot args)
├── security.py    # (v0.5) htpasswd (bcrypt), IP allowlist (stdlib ipaddress)
├── wizard.py      # (v0.4) setup wizard as a list of testable step functions
├── render.py      # (v0.4) Jinja2 env, render-to-file with .bak
├── backup.py      # (v0.5) tar.gz backup/restore (config + certs + firmware dumps,
│                  #        NEVER audio content)
├── doctor.py      # (v0.7) health checks (containers, ports, box-TLS on 443, WebUI,
│                  #        DNS, box certs, LE expiry) via injectable Probes seam
├── menus/         # per-topic submenus
└── templates/     # *.j2 for compose + nginx (shared-443 SNI split / separate WebUI port)
```

### Roadmap

v0.1 skeleton → v0.2 Docker management (adopt existing installs) → v0.3 certificates
(own CA, WebUI client certs issue/renew/revoke + CRL for nginx, PKCS#12 export, dumped
box-cert handling, ca.der export) → v0.4 setup wizard + templates → v0.5 security +
backup → v0.6 Let's Encrypt for the WebUI hostname → v0.7 health check ("doctor").
No PyPI release for now — installation via git URL.
