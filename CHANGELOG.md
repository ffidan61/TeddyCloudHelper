# Changelog

## v0.13.1

- Update notice and the doctor's image check now name what they're actually
  about: "TeddyCloudHelper update available" (the CLI tool) is clearly
  distinct from "TeddyCloud image freshness" (the server container) — the
  previous generic wording made the two easy to confuse

## v0.13.0

- Stream-level access log on port 443: SNI (`$ssl_preread_server_name`) and
  chosen backend (`$upstream_addr`) go to stdout (`docker compose logs
  nginx`) — cuts SNI-routing debugging from hours to minutes
- `teddycloudhelper --doctor --json` — machine-readable check results for
  Uptime Kuma, Healthchecks.io, or custom scripts
- Security menu: allowed IPs can now bypass Basic Auth (`satisfy any`) —
  LAN IPs get in without a password, everyone else still needs one; off by
  default, only offered once both Basic Auth and an IP allowlist are set
- CI now runs the opt-in Docker integration tests
  (`TCH_DOCKER_TESTS=1 pytest -m docker`) on every push/PR — `ubuntu-latest`
  ships Docker already, only `nginx:stable-alpine` needs pulling

## v0.12.0

- nginx config is validated with `nginx -t` in a throwaway container before
  every (re)start; a broken render is rolled back to its last good `.bak`
  and the restart is aborted, so a template regression can never take the
  box path down
- teddycloud compose service has a `curl /api/stats` healthcheck; the doctor
  reports "unhealthy" (fail) and "starting" (warn) from `compose ps`
- Startup shows an update hint when a newer version is tagged on GitHub
  (best-effort, 2s timeout, silent on failure)
- Doctor image-freshness check adds a human-readable version line (running
  image's OCI version label vs. the latest TeddyCloud GitHub release; the
  release comparison is skipped on the `develop` channel)

## v0.11.1

- Doctor: image-freshness check (local digest vs. ghcr registry) with a
  hint at the Docker menu's pull action
- Main menu: "Show known boxes" table (name, MAC, model) via `/api/getBoxes`
- Opt-in Docker integration tests (`TCH_DOCKER_TESTS=1`): rendered nginx
  configs validated with real `nginx -t`, compose files with
  `docker compose config`
- README rewritten (feature overview, cron monitoring, hard-won TeddyCloud
  facts); versions are tagged from now on

## v0.11.0

- Headless doctor: `teddycloudhelper --doctor [--project DIR]` exits 1 on
  failures (cron-friendly); fixed a crash on non-UTF-8 consoles
- The hostname patched into the box firmware is recorded (wizard +
  settings); the doctor checks DNS for that name and probes 443 with that
  exact SNI to catch WebUI collisions
- Doctor offers to create a backup when the backup check warns

## v0.10.1

- Firmware check lists the images from `firmware/` directly (newest first)

## v0.10.0

- Certificate menu: "Check a patched firmware image" — verifies the
  embedded CA matches this instance before flashing
- Doctor: CA-change guard (fingerprint recorded in state, loud failure on
  change, interactive acceptance) and backup-age check

## v0.9.13

- Security menu moved under Project settings

## v0.9.12

- Settings menu shows the current configuration on entry

## v0.9.11

- New Project-settings menu: change hostname, listen mode, deployment mode
  individually; certificates are decided by files on disk, not prompts
  (existing Let's Encrypt certs are kept, client-cert auth is not re-asked)

## v0.9.10

- Wizard recommends the separate WebUI port; shared 443 is labelled
  advanced and explains that the box needs its own hostname there

## v0.9.9

- Port 80 keeps the box API (`/v1/`) reachable — the redirect-everything
  hardening broke the box boot sequence (time is fetched over plain HTTP
  before the first TLS handshake)

## v0.9.8

- First-start 502 notice shown as an orange warn panel (new `ui.warn_panel`)

## v0.9.7

- Live log following (`logs -f`) and a compose exec wrapper
- Doctor: detects TeddyCloud's security-mitigation lock, lists known boxes,
  reads Let's Encrypt expiry from the live certificate (no sudo needed)
- Wizard never finishes with an unprotected WebUI

## v0.9.6

- Port 80 no longer proxies the web GUI to TeddyCloud (internet scanners
  tripped its security-mitigation lock, which also cut off the boxes);
  ACME challenges keep working
- Doctor warns when the WebUI has no protection at all

## v0.9.5

- nginx streams request bodies (`proxy_request_buffering off`): TeddyCloud
  drops body bytes that arrive in the same burst as the headers, so every
  upload through a buffering proxy died with 502

## v0.9.4

- nginx proxy timeouts raised to 600s, response buffering disabled (SSE)

## v0.9.3

- Container paths for content/library fixed to `/teddycloud/data/…`
  (upstream layout — the old paths lost data on image updates); four new
  mounts (custom_img, firmware, cache, plugins)
- Health check ("doctor") added to the main menu
- Log filter by service
- Firmware dumps included in backups
