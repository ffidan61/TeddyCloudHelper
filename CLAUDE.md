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
- Certificate layout on the TeddyCloud side: `certs/server/` and `certs/client/`
  (`c2.der` / `client.der`). Server certs are auto-generated on first container start;
  extract `certs/server/ca.der` afterwards to flash onto the box.
- Connecting a box: dump its original certs → flash the replacement CA → redirect DNS
  (`prod.de.tbs.toys` etc.) to the TeddyCloud host.
- Open question: does TeddyCloud consume a CRL directly, or is client validation purely
  TLS-level? Verify before building revocation UX (v0.3).

## Architecture

Key decisions (confirmed with the maintainer):

| Area | Decision |
|---|---|
| UI | `questionary` prompts + `rich` output; all prompts via `ui.py` (Ctrl-C → `ui.Cancelled`) |
| Docker | `docker compose` CLI via subprocess (args list, never `shell=True`); local host only |
| Certificates | `cryptography` library (no openssl shell-out); htpasswd via `bcrypt` directly |
| Templates | Jinja2 for docker-compose.yml + nginx confs; render-to-file writes timestamped `.bak` first |
| State | `AppState` dataclass → `<project>/teddycloudhelper.json` with `schema_version` + migrations (`state.py`); global "last project" pointer via `platformdirs` |
| Let's Encrypt | not in v1 (box traffic is SNI passthrough; WebUI gets self-signed or user-provided cert) |

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
├── certs/         # (v0.3) ca.py, client_certs.py (PEM+DER), crl.py, server_certs.py
├── security.py    # (v0.5) htpasswd (bcrypt), IP allowlist (stdlib ipaddress)
├── wizard.py      # (v0.4) setup wizard as a list of testable step functions
├── render.py      # (v0.4) Jinja2 env, render-to-file with .bak
├── backup.py      # (v0.5) tar.gz backup/restore (config + certs, NEVER audio content)
├── menus/         # per-topic submenus
└── templates/     # *.j2 for compose + nginx (shared-443 SNI split / separate WebUI port)
```

### Roadmap

v0.1 skeleton (this) → v0.2 Docker management (adopt existing installs) → v0.3 certificates
(CA, issue/renew/revoke, CRL, DER export) → v0.4 setup wizard + templates → v0.5 security +
backup → v0.6+ Let's Encrypt for the WebUI hostname, PyPI release.
