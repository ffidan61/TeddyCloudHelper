# Changelog

## v0.22.0

- Fixed: TeddyCloud kept locking itself ("locked to mitigate security
  risks") after a while, cutting off the boxes — even with the WebUI fully
  protected. Cause: TeddyCloud's crawler check (`security_mit.onCrawler`)
  runs on **every** listener before any authentication, including the box
  port 443, which must stay open to the internet and only optionally asks
  for a client certificate. A single scanner request (Censys, Shodan, Nmap,
  Nuclei, ...) to `https://<your-ip>/` locked the instance until the next
  restart. nginx cannot filter it — the box path is TLS passthrough.
- New Security menu action "Stop internet scanners from locking TeddyCloud"
  (nginx mode with WebUI protection only): turns `security_mit.onCrawler`
  off via TeddyCloud's settings API and saves it to `config.ini`; a locked
  instance is restarted first (the lock blocks the API too). The other
  triggers (robots.txt, blacklisted domains) can only arrive through the
  protected WebUI and stay on.
- New doctor check "Scanner lock": queries the setting inside the
  container, so it sees the lock even behind client-cert auth (the WebUI
  check only saw the TLS rejection and reported "ok").

## v0.21.2

- Fixed v0.21.1's real-IP fix, which did not work: it added the PROXY
  header on an internal loopback hop, and that hop only ever reported
  its own peer — `127.0.0.1`. With an IP allowlist in shared-443 mode
  every client was therefore blocked (403). The PROXY header now comes
  from the 443 server itself (the only place that knows the client
  address); the box path goes through a small internal hop that strips
  the header again, so TeddyCloud still gets the box's raw TLS stream.
  Run "Re-render config files" in Project settings and restart.
- New runtime integration tests (CI, Docker) start the rendered nginx
  next to a fake TeddyCloud and fetch from clients with fixed IPs: the
  allowlist allows/denies by the real client IP in shared and separate
  mode, and the box path (no SNI) reaches TeddyCloud cleanly. `nginx -t`
  alone could not catch the v0.21.1 mistake.

## v0.21.1

- Fixed: in shared-443 mode nginx saw every WebUI request as coming from
  `127.0.0.1` — the 443 stream proxy handed WebUI connections to the
  loopback TLS listener and the client address got lost. The IP allowlist
  and "allowed IPs skip Basic Auth" (`satisfy any`) therefore did not work
  in shared mode, and the access log showed no real client IPs. The WebUI
  branch now goes through an internal stream hop (`127.0.0.1:8445`) that
  adds the PROXY protocol; the TLS listener reads it
  (`real_ip_header proxy_protocol`, trusted from loopback only). The box
  path stays plain passthrough — TeddyCloud does not speak the PROXY
  protocol. Separate-port mode is unchanged. Run "Re-render config files"
  in Project settings and restart to apply.

## v0.21.0

Security fixes from a review of the tool. **After updating, run "Re-render
config files" in Project settings and restart** — items 1 and 2 live in the
templates.

1. **Port 80 no longer exposes TeddyCloud's box API.** nginx proxied all of
   `/v1/` over plain HTTP, and TeddyCloud answers every box-API route
   (`/v1/content`, `/v1/claim`, `/v1/cloud-reset`, ...) on its HTTP port
   without a client-cert check — reachable from the internet. Port 80 now
   proxies only the exact `/v1/time` endpoint the box needs before its
   first TLS handshake.
2. **nginx no longer sees the WebUI CA key.** The whole `webui-pki/` (CA
   key, client keys, `.p12` bundles) was mounted into the internet-facing
   nginx container; a compromised nginx could have minted client
   certificates. It now gets only `server/`, plus `ca/ca.crt` and `crl/`
   when client-cert auth is on. The `nginx -t` pre-check mounts the same.
3. **`.p12` bundles are owner-only (0600)**, created that way from the start
   (keys and the CA key too — no more write-then-chmod window). Sending a
   passwordless `.p12` via the plain-HTTP one-time link now asks first,
   defaulting to "no".
4. **Backups are owner-only (0600)** — they contain every private key of
   the project.
5. **The one-time download link is now strictly single-use** even under
   simultaneous requests (atomic claim instead of check-then-set).
6. **The WebUI hostname is validated** (DNS name or IP only) in the wizard,
   Project settings and the certificate menu, and re-checked on every
   render — a hand-edited state file or a restored backup can no longer
   inject nginx directives or path segments.

Also: client-cert names with a trailing newline are rejected (`fullmatch`).

## v0.20.0

- nginx hardening: the WebUI server block now sets `ssl_session_tickets off;`
  (nginx defaults to on). Ticket-based resumption has been the source of
  several nginx CVEs (CVE-2025-23419, CVE-2026-97629), and with client-cert
  auth a resumed session skips the certificate check. Takes effect after
  "Re-render config files" in Project settings plus a restart.
- New doctor check "Proxy image freshness" (nginx mode only): compares the
  local `nginx:stable-alpine` image — and `certbot/certbot` when Let's
  Encrypt is enabled — against Docker Hub, and detects "pulled but not
  applied". These floating tags only get security fixes with a pull; until
  now the doctor only watched the TeddyCloud image.
- The registry digest probe now understands Docker Hub refs in addition to
  ghcr.io (`doctor.parse_image_ref`).

## v0.19.0

- Project settings can now switch a deployment from direct to nginx mode
  without re-running the whole setup wizard: the "Switch deployment mode"
  action runs the wizard's nginx steps inline (WebUI hostname, self-signed
  certificate, optional client-cert auth, access protection), then
  re-renders and restarts. Let's Encrypt stays a separate step in the
  certificate menu. (Previously this direction was a dead end that only
  pointed back at the wizard.)
- New main-menu action "Switch project": pick among recently used project
  directories instead of re-typing the path every time (for setups running
  several instances, e.g. tc2/tc3). The global config now keeps a
  `recent_projects` list (newest first, deduplicated, capped at 10,
  gone-directory entries dropped on read); an existing config with only
  `last_project` is upgraded automatically on the next save. The startup
  banner hints at the switcher when more than one project is known.

## v0.18.1

- Fixed the PUID/PGID prompt default when the tool runs via sudo: the
  effective UID is 0 there — not a valid PUID — so the prompt rejected
  its own default forever. The default is now the user who invoked sudo
  (`SUDO_UID`/`SUDO_GID`); a plain root login falls back to 1000.

## v0.18.0

- New "Run TeddyCloud as non-root (PUID/PGID)" option — asked once in the
  setup wizard (after the image channel) and toggleable under Project
  settings: uses TeddyCloud v0.7.0's entrypoint support to run the server
  as a host user instead of root (default: the invoking user's UID/GID).
  Older images simply ignore the variables and keep running as root, so
  the option is safe on any channel. The prompts warn that the entrypoint
  runs `chown -R` over /teddycloud on every container start — ownership
  of the project's data directories on the host changes accordingly, and
  the first start can take a while with a large library.

## v0.17.0

- New certificate-menu action "Send a client certificate to a phone
  (one-time link + QR)": serves the chosen `.p12` exactly once at an
  unguessable token URL on an ephemeral port (max. 5 minutes, gone after
  the download, the timeout, or Ctrl-C) and renders the link as a
  scannable QR code in the terminal. The machine's primary IP is detected
  as the default address and can be edited (VPN / other interface). The
  trust model is explained up front: anyone seeing the link during the
  window can fetch the file — the `.p12` password still protects the key
  itself. New dependency: `qrcode` (pure Python).

## v0.16.3

- Switching to direct mode (settings or wizard) now resets the Let's
  Encrypt state (`webui_tls_mode` back to self-signed, certbot flag off):
  direct mode has no certbot, so the certificate could never renew and the
  tool would warn about the inevitable expiry at every start — forever.
  Certificates stay on disk; switching back to nginx picks them up again.
- The final Let's Encrypt switch-over now goes through the validated
  restart path (`nginx -t` first): a broken switch (e.g. `privkey.pem`
  missing although `fullchain.pem` exists) is caught instead of
  crash-looping nginx and taking the box path down.
- The WebUI port prompt rejects 80 and 443 — the generated compose file
  would publish the port twice and fail to start with a cryptic error.
- nginx now re-resolves the teddycloud container at runtime (Docker DNS
  resolver + variable `proxy_pass` everywhere, both stream and http): a
  manual `docker compose pull && up` outside the tool no longer risks nginx
  proxying to the dead IP of the replaced container.
- Old render backups are pruned: the newest 10 `.bak` files per config are
  kept instead of accumulating forever.

## v0.16.2

- Reissuing a client certificate under an existing name now revokes the old
  serial in the same step (and offers the restart nginx needs to enforce
  it). Previously the old certificate's files were overwritten, making its
  serial unrevokable via the menu — while nginx kept accepting it
  (CA-signed, not expired, never on the CRL).
- "Re-render config files" now asks before replacing a compose file that was
  not generated by this tool — the doctor's mount check points adopted
  installs (hand-written compose) at exactly this action.
- A leftover `nginx/nginx.conf` from an earlier nginx-mode render no longer
  blocks restarts in direct mode: the pre-restart `nginx -t` validation is
  skipped when the project's deployment mode is not nginx (no container
  uses the file).

## v0.16.1

- Moved "Switch TeddyCloud image channel (latest / develop)" from the Docker
  menu into Project settings — it is a configuration change (state +
  re-render + restart cycle) like everything else there, not a container
  operation. Behaviour is unchanged, including the foreign-compose warning
  and the pull-and-restart offer.

## v0.16.0

- Fixed four "change happens but running containers never see it" bugs,
  found in a full-project review:
  - Docker menu "Pull latest images" ran `compose restart`, which keeps the
    old image — the new image was never used (only `up` recreates changed
    containers).
  - After recreating just the teddycloud container (image update), nginx
    could keep proxying to the old container IP (it resolves the hostname
    only at startup) — 502 on the WebUI, and in separate-port mode a dead
    box path — until some later restart.
  - Backup restore only ran `up`: with an unchanged compose definition, the
    restored bind-mounted configs (nginx.conf, htpasswd, webui-pki) were
    never actually applied, and the restored nginx.conf was never validated.
  - Revoking a client certificate rewrote the CRL but never offered the
    restart nginx needs to enforce it — the revoked certificate silently
    kept working.
- All apply paths (settings, security, docker pull/channel switch, backup
  restore, certificate revocation) now go through one shared
  `restart_services()` helper: `nginx -t` validation, then `up`, then
  `restart`.
- Doctor: the image-freshness check now also compares the running
  container's image ID against the local tag and warns "pulled but never
  applied".
- Backups now include all compose filename variants (`compose.yaml` etc.),
  not just `docker-compose.yml` — adopted installs keep their original name.

## v0.15.2

- Fixed the version the tool reports for itself: `__version__` was a
  hardcoded string that release bumps forgot (stuck at 0.14.0), so an
  up-to-date install kept nagging about an "update" to itself. It is now
  derived from the installed package metadata — `pyproject.toml` is the
  single version source — and a test pins the two together.

## v0.15.1

- Fixed the "TeddyCloudHelper update available" notice at startup: it
  queried the GitHub tags API anonymously, which answers 404 since the
  repo went private — the check had been silently dead since then. It now
  falls back to `git ls-remote --tags` over the same SSH URL the tool is
  installed from (batch mode + timeouts, so startup can never hang on an
  ssh prompt); the API stays the first choice and takes over again once
  the repo goes public.
- The notice now names the actual upgrade command
  (`uv tool upgrade teddycloudhelper`) instead of the vague
  "reinstall from the git URL".

## v0.15.0

- Fixed WebUI custom-tonie image uploads: TeddyCloud serves these images
  from `www/custom_img` but its Image Manager uploads them into
  `library/custom_img` — uploads 500'd (directory missing) and, once it
  existed, images never showed up. The compose template now binds the same
  host `./custom_img` directory to both container paths, so uploads and
  serving share one directory.
- New doctor check "Container mounts": inspects the running teddycloud
  container live and fails when a mount expected by the current template
  is missing (stale or hand-edited compose) or when the two custom_img
  paths come from different host directories. A test pins the template's
  mounts to the doctor's expectation so neither can drift silently.
- New Project-settings action "Re-render config files (after a
  TeddyCloudHelper update)": regenerates compose + nginx configs from the
  current templates without changing any setting — the fix path the new
  doctor check points to.

## v0.14.0

- Removed the "hostname patched into the box firmware" feature (wizard
  prompt, settings menu entry, state field, SNI-collision and custom-DNS
  doctor checks) — it only mattered for firmware patched to use a custom
  hostname instead of `prod.de.tbs.toys`, a case nobody actually hits since
  real Tonieboxes send no SNI at all. The doctor's DNS check now always
  checks `prod.de.tbs.toys`.

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
