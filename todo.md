# TODO — Kandidaten

Stand: 2026-10-26, v0.21.2. Offene Punkte oben, erledigte unten zur Historie.
Jeder offene Punkt ist so beschrieben, dass ein Entwickler oder eine andere KI
ihn ohne Rückfrage umsetzen kann (betroffene Dateien, Ist-Zustand, Soll-Zustand).

## A — Offen: konkrete Implementierungen

### 1. Custom-Tonies-Menüpunkt

**Neue/betroffene Dateien:** neue Datei
`src/teddycloudhelper/menus/tonies.py` (eigenes Modul, nicht in `project.py`
mischen — Muster wie `menus/backup.py`: ein Modul pro Thema mit eigener
`MENU`-Liste und `run(state, project)`-Einstiegspunkt). Einbindung in
`src/teddycloudhelper/menus/__init__.py` bzw. wo die anderen Menüs im
Hauptmenü (`cli.py`) registriert werden — dort nach dem Muster der
bestehenden Einträge (Docker, Certs, Security, Backup, Doctor, Boxes,
Settings, Project) einen weiteren Eintrag "Custom Tonies" hinzufügen.

**Ist-Zustand:** Kein Menüpunkt dafür. Die Datei `config/tonies.custom.json`
wird von TeddyCloud selbst gelesen (per Bind-Mount `./config:/teddycloud/config`
im Projektverzeichnis sichtbar, siehe `templates/docker-compose.yml.j2`),
aber TeddyCloudHelper bietet keine Verwaltung dafür an. Format laut
TeddyCloud-Doku: ein JSON-Array von Objekten mit u. a. `"no"` (Tonie-ID/
Model-Nummer), `"audio_id"`, `"hash"` — TeddyCloudHelper muss diese Struktur
NICHT verstehen, nur die Datei als Ganzes anfassen.

**Soll-Zustand — konkreter Menüablauf** (analog zu `menus/backup.py` als
Vorlage für Aufbau: `MENU`-Konstante als Liste von `(Label, key)`-Tupeln,
`_HANDLERS`-Dict, `run()`-Funktion mit `ui.menu()`-Schleife):

```python
CUSTOM_TONIES_REL = Path("config") / "tonies.custom.json"

MENU = [
    ("Show file path / current content", "show"),
    ("Create empty tonies.custom.json (if missing)", "create"),
    ("Reload TeddyCloud to pick up changes", "reload"),
    ("Back to main menu", "back"),
]
```

1. **`show`**: Pfad `project / CUSTOM_TONIES_REL` anzeigen
   (`ui.info_panel`); falls die Datei existiert, Größe + Anzahl Top-Level-
   Array-Einträge (`len(json.loads(path.read_text()))`) mitausgeben, mit
   `try/except json.JSONDecodeError` → dann Warnung "Datei ist kein gültiges
   JSON, TeddyCloud wird sie vermutlich ignorieren" statt Absturz.
2. **`create`**: nur anbieten, wenn die Datei noch NICHT existiert
   (`if path.exists(): ui.info_panel("Already exists — nothing to do."); return`).
   Sonst `path.parent.mkdir(parents=True, exist_ok=True)` (falls
   `config/` noch fehlt, z. B. vor dem ersten Container-Start) und
   `path.write_text("[]\n", encoding="utf-8")` nach `ui.confirm(...)`.
3. **`reload`**: Wiederverwendung des bestehenden Restart-Bausteins —
   `wizard.restart_services(project)` (siehe `wizard.py` Zeile 419, gleiche
   Funktion, die `menus/settings.py::_apply()` nutzt) hinter einer
   `ui.confirm("Restart teddycloud now to apply changes?")`-Abfrage. KEIN
   eigenes Reload-Signal an TeddyCloud senden (kein bekannter Reload-Endpoint
   dafür) — ein Container-Restart ist der einzige verlässliche Weg, das
   File neu einzulesen.

**Bewusst NICHT tun:** kein JSON-Schema, kein interaktiver Editor, keine
Validierung der Tonie-Feldstruktur — TeddyCloud validiert selbst beim
Einlesen. Diese Zurückhaltung ist im unteren Abschnitt "Bewusst NICHT
übernehmen" schon so vermerkt und gilt hier genauso.

**Test:** `tests/test_menus_tonies.py` — `create` auf leerem Projektverzeichnis
prüft `Path("config/tonies.custom.json").read_text() == "[]\n"`; `create` bei
bereits vorhandener Datei verändert den Inhalt NICHT (Regressionsschutz gegen
versehentliches Überschreiben von Nutzerdaten).

---

### 2. Support-Paket (anonymisiertes Debug-ZIP)

**Vorbild:** TeddyCloudStarter `utilities/support_features.py` (siehe Analyse
2026-07-06). **Neues Modul:** `src/teddycloudhelper/support.py` (Business-
Logik, kein UI-Code — Muster wie `backup.py`, das reinen Logik-Code ohne
`ui.*`-Aufrufe enthält) + `src/teddycloudhelper/menus/support.py` (dünner
Menü-Wrapper, Muster wie `menus/backup.py`).

**Ist-Zustand:** `backup.py` (siehe Volltext oben im vorigen Abschnitt für das
Muster `BACKUP_ITEMS`/`create_backup`/`_unique_path`/`_validate_member`) packt
config+certs für Backups, aber unverändert und ohne Anonymisierung — dafür
auch nicht gedacht, das Backup MUSS die echten Zertifikate enthalten, damit
ein Restore funktioniert. Es gibt kein separates Bundle für Hilfe-Anfragen
(forum.revvox.de / Telegram), bei denen der Inhalt für Fremde lesbar wird.

**Verfügbare Bausteine, die wiederverwendet werden können:**
- `docker_cli.Compose(project).logs(tail=..., service=...)` (siehe
  `docker_cli.py` Zeile 197–203) liefert Log-Text eines einzelnen Service
  als `str` — pro Service (`teddycloud`, `nginx`, `certbot` falls
  `state.letsencrypt_enabled`) einmal mit z. B. `tail=500` aufrufen.
- `docker_cli.Compose(project).ps()` liefert `list[ServiceStatus]` — für
  einen "Status zum Zeitpunkt der Erstellung"-Textblock im Bundle nützlich.
- `backup._unique_path`-Muster (Zeitstempel im Dateinamen, Kollisions-Suffix)
  1:1 für den ZIP-Dateinamen übernehmen, nur Endung `.zip` statt `.tar.gz`
  und anderes Präfix, z. B. `teddycloudhelper-support-<stamp>.zip`.

**Soll-Zustand — Aufbau des ZIPs** (ZIP statt tar.gz: Forum-Uploads/
Telegram-Anhänge sind meist auf ZIP ausgelegt und lassen sich ohne
Kommandozeile öffnen):

```
teddycloudhelper-support-20260912-153000.zip
├── logs/
│   ├── teddycloud.log        # docker_cli.Compose(project).logs(tail=500, service="teddycloud")
│   ├── nginx.log             # nur wenn deployment_mode == "nginx"
│   └── certbot.log           # nur wenn letsencrypt_enabled
├── configs/
│   ├── docker-compose.yml    # so wie im Projektverzeichnis gerendert
│   ├── nginx.conf            # falls vorhanden
│   └── teddycloudhelper.json # der komplette State — enthält KEINE Secrets,
│                              # nur Hostnamen/Modi/Flags (siehe AppState-Felder)
├── certs_overview.txt        # NUR Metadaten: Dateiname + Ablaufdatum je Zertifikat
│                              # (aus client_certs.list_client_certs() /
│                              # server_certs, NICHT der Zertifikatsinhalt)
└── tree.txt                  # Verzeichnisbaum des Projektordners, nur Pfade,
                                # keine Dateiinhalte
```

Alle Text-Dateien (`*.log`, `*.yml`, `*.conf`, `*.json`, `*.txt`) laufen vor
dem Einpacken durch die Anonymisierungs-Funktion (siehe unten); `tree.txt`
selbst enthält nur Namen, keine Inhalte, muss also nicht anonymisiert werden
— außer der Projektpfad selbst einen Hostnamen/Nutzernamen preisgibt (z. B.
`C:\Users\<Name>\...` unter Windows) — auch den Wurzelpfad in `tree.txt`
durch einen Platzhalter wie `<project>` ersetzen.

**Kernstück — Anonymisierung** (eigene Funktion `support.anonymize(text: str,
state: AppState) -> str`, wird auf jede Text-Datei angewendet BEVOR sie ins
ZIP geschrieben wird — nie nachträglich am fertigen Archiv arbeiten):

1. **Hostnamen gezielt** — `state.webui_hostname` (und, falls vorhanden, die
   Box-DNS-Namen aus `state`) als literale String-Ersetzung durch
   `<webui-hostname>` — NICHT pauschal per Regex "sieht aus wie ein
   Hostname", das würde z. B. `docker.io` in einer Log-Zeile mit-zerstören
   und die Logs für die Fehlersuche unbrauchbar machen.
2. **IPv4/IPv6-Adressen** — Regex-Suche, Treffer durch `stdlib ipaddress.
   ip_address(match)` validieren (verhindert False Positives wie
   Versionsnummern `1.2.3.4`-artige Strings, die keine echte IP sind — analog
   zur Validierung, die `security.py` für die IP-Allowlist nutzt), dann durch
   `<ip>` ersetzen. Öffentliche vs. private IPs NICHT unterscheiden — beide
   redigieren, das Muster (LAN vs. WAN) ist für die Fehlersuche meist
   irrelevant, für den Nutzer aber schützenswert.
3. **MAC-Adressen** — Regex `([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}` durch
   `<mac>` ersetzen. Wichtig: die Box-CN in TeddyCloud-Logs UND in
   `/api/getBoxes`-Antworten (falls die im State/Cache vorkommen) IST die
   MAC-Adresse — das ist der Hauptgrund für diesen Schritt, nicht nur
   Netzwerk-Interfaces.
4. **E-Mail-Adressen** — einfache Regex `[\w.+-]+@[\w-]+\.[\w.-]+` durch
   `<email>`.
5. **Secrets/Tokens** — Zeilen, die nach `Authorization: Basic `, einem
   PEM-Block (`-----BEGIN ... KEY-----` bis `-----END ... KEY-----`) oder
   einem Bcrypt-Hash (`$2[aby]$...`, wie sie `security.py` für Basic-Auth
   erzeugt) aussehen, KOMPLETT durch `<redacted>` ersetzen (nicht nur den
   Wert — bei PEM-Blocks die ganze mehrzeilige Sektion).

**Hartes Ausschlusskriterium (wichtigste Regel, direkt aus dem TCS-Vorbild):**
`.key`-Dateien, `private.der`, und jede Datei unterhalb von `certs/` mit
privatem Schlüsselmaterial werden **NIE** in das ZIP aufgenommen — auch nicht
in anonymisierter Form. Nur `certs_overview.txt` mit Dateiname + Ablaufdatum
darf auf diese Dateien verweisen, niemals ihr Inhalt.

**Test:** `tests/test_support.py`:
1. `test_anonymize_redacts_ip_mac_hostname_email` — String mit bekannten
   Mustern rein, `assert` dass keines der Originale im Ergebnis mehr auftaucht.
2. `test_anonymize_keeps_unrelated_version_strings` — Regressionstest gegen
   False Positives (z. B. `TeddyCloud v1.2.3` darf NICHT als IP erkannt
   werden — das ist der Fall, den die `ipaddress`-Validierung aus Schritt 2
   abfangen soll).
3. `test_bundle_never_contains_private_key_material` — Bundle in einem
   `tmp_path`-Projekt mit echten (Test-)Zertifikaten erzeugen, ZIP-Inhalt
   auflisten (`zipfile.ZipFile(path).namelist()`), `assert` dass kein Eintrag
   auf `.key`/`private.der` endet oder `-----BEGIN` in seinem Inhalt hat —
   das ist der Test, der die wichtigste Regel dieses Punkts hart absichert.

---

### 3. Self-Update aus dem Tool heraus

**Ist-Zustand:** `cli.py::main()` zeigt beim Start nur
`updates.update_notice(__version__)` an (gelber Hinweis mit "Run 'uv tool
upgrade teddycloudhelper'"). Das Update selbst muss der Nutzer in der Shell
ausführen.

**Soll-Zustand:** Hauptmenü-Eintrag "Update TeddyCloudHelper" in
`MENU_ACTIONS` (`cli.py`) — nur einblenden, wenn `update_notice` beim Start
eine neuere Version gemeldet hat (Ergebnis in einer Variable merken, nicht
erneut abfragen). Ablauf:
1. `ui.confirm("Update to vX.Y.Z now?")`
2. `subprocess.run(["uv", "tool", "upgrade", "teddycloudhelper"])` als
   Args-Liste (nie `shell=True`), Ausgabe live durchreichen. Fehlt `uv` im
   PATH (`shutil.which("uv") is None`) → Fehler-Panel mit dem manuellen
   Befehl, kein Absturz.
3. Bei Erfolg: Hinweis "Updated — please restart teddycloudhelper" und das
   Programm beenden (`_dispatch` gibt False zurück). Ein Neustart ist nötig,
   weil der laufende Prozess noch den alten Code geladen hat; ein
   automatisches `os.execv` lieber nicht (unter sudo/Windows fehleranfällig).
4. Ziel ist Linux/Prod; unter Windows kann uv die laufende `.exe` evtl.
   nicht ersetzen → Fehler sauber anzeigen.

**Offene Prüfung vorab:** Klären, ob `uv tool upgrade` bei einer
git+ssh-Installation (ohne gepinnten Ref) zuverlässig den neuesten Commit
zieht. Falls nicht: stattdessen `uv tool install --force
"git+ssh://git@github.com/ffidan61/TeddyCloudHelper"` (wie in der README)
ausführen — und dann auch den Text in `updates.update_notice` anpassen.
Außerdem: unter `sudo` gestartet → Upgrade würde roots uv-Installation
treffen statt die des Nutzers; dann Hinweis statt Ausführung.

**Test:** `tests/test_cli.py` — Runner als Seam injizierbar machen (Muster
wie `docker_cli`), prüfen: Menüpunkt nur bei verfügbarem Update; korrekte
Args-Liste; Exit-Code ≠ 0 → Fehler-Panel, Menü läuft weiter.

---

### 4. Offen aus dem IP-Fix (v0.21.2): Folgepunkte

- Separate-Port-Modus: Docker erhält bei IPv4 die Quell-IP (iptables-DNAT).
  Bei IPv6 über den userland-proxy (`docker-proxy`) kommt dagegen die
  Gateway-IP an → IP-Regeln greifen bei IPv6-Clients nicht. Dokumentieren,
  ggf. Doctor-Hinweis, wenn der WebUI-Hostname einen AAAA-Record hat.
- Der Doctor prüft die WebUI von localhost bzw. aus dem Docker-Netz — seine
  Probe ist nie eine "echte" Client-IP. Bei aktiver Allowlist bzw. künftigem
  Home-Modus bekommt er 403 und muss das als ok werten
  (`doctor.py::check_webui`, heute nur 400/401 behandelt).

---

### 5. Mögliches Feature: DNS-Namen (DynDNS) in der IP-Allowlist

**Idee (mit dem User besprochen 2026-10-26):** Die Allowlist akzeptiert
zusätzlich Hostnamen wie `home.example.dyndns.org`. Grund: Bei dynamischer
Heim-IP (z. B. Zwangstrennung bei DSL) sperrt die Allowlist sonst nach jedem
IP-Wechsel aus, bis die neue IP von Hand eingetragen ist.

**Kernproblem:** nginx kann in `allow` keine Hostnamen — das Tool muss den
Namen selbst auflösen und die IPs in die Config schreiben.

**Skizze:**
- *State* (`state.py`): neues Feld `ip_allowlist_hostnames: list[str]`
  (getrennt von `ip_allowlist`, damit die bestehende IP-Validierung über
  `security.normalize_allowlist_entry` unverändert bleibt). Validierung der
  Namen über `server_certs.validate_webui_hostname`. Die aufgelösten IPs sind
  abgeleitet → NICHT in den State.
- *Generierte Include-Datei* `nginx/allowlist-dns.conf` mit `allow <ip>;`-
  Zeilen, eingebunden im WebUI-Server VOR `deny all;`. Dient zugleich als
  "last known good":
  - schreibt nur bei Änderung (sonst unnötige Reloads/`.bak`s);
  - in place schreiben (truncate statt rename), weil einzeln bind-gemountet —
    ein Rename sieht der Container nicht;
  - **schlägt die DNS-Auflösung fehl, Datei NICHT anfassen** — sonst sperrt
    ein DNS-Hänger alle aus;
  - `render_project` legt sie immer an (notfalls nur Kommentar), sonst erzeugt
    Docker an ihrer Stelle einen Ordner.
- *Compose:* Mount `./nginx/allowlist-dns.conf:/etc/nginx/allowlist-dns.conf:ro`;
  gleicher Mount in `docker_cli.nginx_config_test` und im
  Integrationstest-Helper.
- *Neues Modul* (z. B. `access.py`, Logik ohne UI — wird von A6
  wiederverwendet): `resolve_hostnames()` (nur A-Records, injizierbarer
  Resolver als Test-Seam), `write_dns_allowlist()` (Rückgabe: geändert ja/nein),
  `maintain(project)` (Cron-Job: neu auflösen, bei Änderung nginx neu laden),
  `reload_nginx()` (`docker_cli.nginx_config_test`, dann
  `Compose.exec_service("nginx", "nginx", "-s", "reload")` — kein
  Container-Neustart, die Box merkt nichts).
- *Headless:* `teddycloudhelper --maintain [--project P]` (Muster
  `_headless_doctor` in `cli.py`), eine Zeile pro Aktion, Exit ≠ 0 bei Fehlern
  (Cron schickt dann Mail). Ohne Hostnamen-Einträge: no-op.
- *Menü* (`menus/security.py`): "Add an IP allowlist entry" akzeptiert IP,
  Netz ODER Hostnamen; bei Hostnamen sofort testweise auflösen und die IPs
  anzeigen; danach die fertige Cron-Zeile mit absolutem Pfad
  (`shutil.which("teddycloudhelper")`) anzeigen:
  `*/5 * * * * /pfad/teddycloudhelper --maintain --project /proj >/dev/null`.
  "Show security status" zeigt Hostname → aktuelle IPs.
- *Doctor:* neuer Check, nur wenn Hostnamen eingetragen sind: Name nicht
  auflösbar → warn; aufgelöste IPs ≠ Inhalt von `allowlist-dns.conf` → warn
  ("Cron läuft offenbar nicht"). Zusätzlich A4-Punkt: 403 der eigenen Probe
  bei aktiver Allowlist als ok werten.

**Bekannte Nebenwirkungen (in der UI erwähnen):**
- Nach einem IP-Wechsel kurze Aussperrung bis zum nächsten Cron-Lauf +
  DNS-TTL (typisch wenige Minuten).
- Die alte IP bleibt solange erlaubt und könnte einem Fremden zugeteilt sein —
  unkritisch, wenn zusätzlich Client-Zertifikate aktiv sind.
- IPv6: DynDNS-AAAA ist meist nur die Router-Adresse, Geräte haben eigene
  IPv6-Adressen → nur IPv4 zuverlässig.

**Test:** `test_access.py` (Fake-Resolver: Änderung → schreibt + reload;
gleiche IPs → kein Schreiben; DNS-Fehler → Datei unverändert),
`test_render.py` (Include vor `deny all`), Laufzeittest in
`test_docker_integration.py` analog `test_runtime_allowlist_sees_real_client_ip`
(Include-Datei mit der Client-IP → 200, ohne → 403). Wie bei allen
Proxy-Änderungen: erst über Branch + Draft-PR durch CI, dann mergen.

---

### 6. Mögliches Feature: Zugangsmodus "Home" / "Reise" für die WebUI

**Idee (mit dem User abgestimmt 2026-10-26):**
- **Home:** Heim-IPs kommen OHNE Zertifikat rein, ALLE anderen IPs sind
  komplett gesperrt (403) — auch mit gültigem Zertifikat. Zuhause ist die
  WebUI damit faktisch nicht aus dem Internet erreichbar.
- **Reise:** alle IPs mit gültigem Zertifikat; Heim-IPs behalten den Bypass
  (Familie zuhause kann weiter ohne Zertifikat).
- Heim-IP ist **dynamisch** → DynDNS-Hostname, regelmäßig aufgelöst.
- Umschalten per **Menüpunkt**; Reisemodus mit **Ablaufdatum**, fällt per
  Cron automatisch auf Home zurück.

**Voraussetzung:** echte Client-IP im shared-Modus — erledigt in v0.21.2;
der Doctor-Punkt aus A4 muss mit umgesetzt werden. Baut auf A5 auf
(DynDNS-Auflösung, `--maintain`, `reload_nginx` — dort zuerst umsetzen).

**Skizze:**
- *State* (`state.py`, Felder mit Defaults, keine Migration nötig):
  `access_mode` (`"off"`/`"home"`/`"travel"`), `home_hostname`,
  `home_networks: list[str]` (statische Zusatz-Netze), `travel_until`
  (ISO-UTC). Aufgelöste IPs sind abgeleitet → NICHT in den State, sondern in
  die generierte Datei `nginx/home-networks.conf` (dient zugleich als
  "last known good").
- *Neues Modul `access.py`* (Logik ohne UI): `resolve_home()` (nur A-Records,
  injizierbarer Resolver), `render_home_networks()` (schreibt nur bei
  Änderung; in place, weil einzeln bind-gemountet; bei DNS-Fehler Datei NICHT
  anfassen), `travel_expired()`, `maintain()` (Cron-Job: DynDNS neu auflösen,
  abgelaufene Reise → Home), `reload_nginx()` (`nginx_config_test`, dann
  `Compose.exec_service("nginx", "nginx", "-s", "reload")` — kein
  Container-Neustart, Box merkt nichts).
- *nginx-Template* (nur bei `access_mode != "off"`, setzt Client-Certs voraus):
  ```nginx
  geo $tch_home { default 0; include /etc/nginx/home-networks.conf; }
  map $ssl_client_verify $tch_cert_ok { SUCCESS 1; default 0; }
  map "$tch_home$tch_cert_ok" $tch_allow {   # bewusst ohne Regex (CVE-2026-42533)
      "10" 1; "11" 1;                          # Heim-IP: immer
      "01" {{ 1 if travel else 0 }};           # Fremd + Zertifikat: nur Reise
      default 0;
  }
  ```
  Im WebUI-Server: `ssl_verify_client optional;` statt `on` (Heimgeräte ohne
  Zertifikat kommen durch den Handshake; widerrufene Zertifikate → weiterhin
  400 über die CRL) und `if ($tch_allow = 0) { return 403; }`. Basic Auth
  bleibt unabhängig aktiv.
- *Compose:* Mount `./nginx/home-networks.conf:/etc/nginx/home-networks.conf:ro`;
  `render_project` legt die Datei immer an (sonst erzeugt Docker einen
  Ordner). Gleicher Mount in `docker_cli.nginx_config_test`.
- *Bestehende Allowlist:* schließt sich mit dem Zugangsmodus aus — beim
  Einschalten Einträge nach Bestätigung nach `home_networks` übernehmen,
  `ip_bypasses_basic_auth` zurücksetzen; `_add_ip` lehnt dann ab.
- *Menü* (`menus/security.py`): "Access mode (home / travel)" — Einrichten
  (DynDNS-Name, Test-Auflösung anzeigen), "Switch to TRAVEL" (Dauer, Default
  14 Tage), "Switch to HOME" (Warnung: von unterwegs sperrst du dich aus),
  statische Netze verwalten. Beim Einrichten fertige Cron-Zeile anzeigen:
  `*/5 * * * * /pfad/teddycloudhelper --maintain --project /proj`.
  Banner zeigt "Access mode: HOME" / "TRAVEL until …"; interaktiver Start
  prüft das Ablaufdatum als Sicherheitsnetz.
- *Headless:* `--maintain` (Muster `_headless_doctor` in `cli.py`), Exit ≠ 0
  bei Fehlern.
- *Doctor:* 403 als ok werten (siehe A4); neuer Check "Access mode": Reise
  abgelaufen aber aktiv / DynDNS nicht auflösbar / aufgelöste IP ≠ Datei
  (→ Cron läuft nicht) / WebUI-Hostname mit AAAA-Record.

**Einschränkung IPv6:** DynDNS-AAAA ist nur die Router-Adresse, Heimgeräte
nutzen eigene IPv6-Adressen → im Home-Modus gesperrt, wenn der
WebUI-Hostname einen AAAA-Record hat. Version 1 daher nur IPv4 (+ Doctor-Warnung).

**Verifikation auf Prod:** Home → Handy im Mobilfunk 403, Heim-WLAN ohne
Zertifikat ok; Reise (1 Tag) → Mobilfunk mit Zertifikat ok, nach Ablauf +
`--maintain` wieder 403; Box-Neustart bootet unverändert.

---

## B — Testen auf dem Prod-Server (keine Implementierung, nur Durchführung)

- [ ] Docker-Integrationstests einmal laufen lassen:
      `TCH_DOCKER_TESTS=1 uv run pytest -m docker`
      (auf dem Server; auf Windows ohne Docker ungetestet geschrieben)
- [ ] Client-Cert-Roundtrip: revoke → nginx-Restart → Browser ausgesperrt (CRL)
- [ ] Backup → Restore einmal komplett durchspielen
- [ ] Cron-Eintrag für `teddycloudhelper --doctor --json` anlegen (Punkt aus
      Abschnitt A ist seit v0.13.0 erledigt, jetzt nur noch der Cron-Job selbst)

## C — v2.0.0-Ideen (größer, erst nach Stabilisierungsphase)

- Shared-443 robuster machen: explizites `'' → teddycloud`-Mapping im
  Template dokumentieren (mlohr-Ansatz; funktional heute schon so, weil
  Boxen kein SNI senden — empirisch am 2026-07-05 bestätigt)
- TCS-"extended mode"-Äquivalent prüfen: nginx terminiert Box-TLS auf
  eigenem Port mit pro-Box-Client-Certs → unbekannte Boxen abweisen
  (Aufwand hoch, Nutzen fraglich für Einzel-Box-Setups)
- Geführter "Box verbinden"-Ablauf: Schritt-für-Schritt ESP32/CC3200 mit
  Verweis auf Firmware-Check + Doctor nach jedem Schritt

**Bewusst NICHT übernehmen:** i18n/Übersetzungen (TCS hat de/en gettext;
CLAUDE.md sagt English only), tonies.custom.json-*Injektion* (bei uns per
Bind-Mount überflüssig, nur Komfort-Menüpunkt siehe A2), Extended-Mode
(siehe C).

## Notizen

- Version-Routine: bump (pyproject + __init__), `uv lock`, CHANGELOG-Eintrag,
  commit, push, `git tag vX.Y.Z && git push --tags`
- `certs/` in Prod NIE anfassen — CA-Wächter schlägt sonst an (Absicht)

---

## Erledigt (Historie)

- ✅ **v0.21.2 — Echte Client-IP im shared-443-Modus** (ursprünglich A4,
  gefunden 2026-10-26): Der Stream-Proxy reichte WebUI-Verbindungen an
  `127.0.0.1:8444` weiter, nginx sah dadurch nur `127.0.0.1` → IP-Allowlist
  und `satisfy any` waren im shared-Modus wirkungslos. Erster Fix in
  v0.21.1 war FALSCH (PROXY-Header erst auf einem Loopback-Hop gesetzt →
  meldet nur 127.0.0.1; auf Prod sperrte die Allowlist dadurch alle aus).
  Richtig (v0.21.2): Der 443-Server selbst sendet `proxy_protocol on`;
  WebUI-Listener `8444 ssl proxy_protocol` + `real_ip_header
  proxy_protocol`; Box-Hop `127.0.0.1:8445` (`listen … proxy_protocol`,
  ohne `proxy_protocol on`) entfernt den Header vor TeddyCloud.
  Lehre: Proxy-/Netzwerk-Änderungen brauchen einen Laufzeittest, `nginx -t`
  reicht nicht → `test_runtime_*` in `test_docker_integration.py`.
- ✅ **v0.21.0 — Security-Review**: Port 80 nur noch `= /v1/time`; nginx
  mountet nur `webui-pki/server`, `ca/ca.crt`, `crl`; Keys/.p12/Backups 0600;
  Einmal-Link atomar; Hostname-Validierung.
- ✅ **v0.20.0** — `ssl_session_tickets off`, Doctor-Check
  "Proxy image freshness" (nginx/certbot gegen Docker Hub).

- ✅ **v0.19.0 — Settings: "direct → nginx" ohne vollen Wizard**
  (ursprünglich Punkt A1): `menus/settings.py::_mode()` ruft für den
  direct→nginx-Wechsel `_switch_to_nginx()` auf, das die Wizard-Steps
  `step_webui` / `step_webui_auth` / `step_first_client_cert` /
  `step_protection` inline durchläuft und dann `_apply()` (re-render +
  Restart) ausführt. Let's Encrypt bleibt bewusst dem Zertifikatsmenü
  überlassen.
- ✅ **v0.19.0 — Multi-Projekt-Umschalter** (ursprünglich Punkt A3):
  Hauptmenü-Aktion "Switch project"; `state.py` führt eine
  `recent_projects`-Liste (neueste zuerst, dedupliziert, Cap 10,
  gelöschte Verzeichnisse beim Lesen entfernt), Legacy-Config mit nur
  `last_project` wird beim nächsten Speichern automatisch aufgewertet;
  `menus/project.py::switch_project()` mit Recent-Picker + "Other
  directory…"-Fallback; Startup-Hinweis bei >1 bekannten Projekten.
- ✅ v0.16.3 — LE-State-Reset bei direct-Mode-Wechsel, validierter
  LE-Switch, Port-Guard, Runtime-DNS in nginx, `.bak`-Pruning
- ✅ v0.16.0 — Apply-Path-Fixes (`restart_services`) + Doctor Image-ID-Check
- ✅ v0.14.0 — Box-Hostname-Feature entfernt (nie genutzter Sonderfall,
  reale Toniebox sendet kein SNI)
- ✅ v0.13.1 — Update-Hinweis vs. Image-Freshness klarer benannt
- ✅ **v0.13.0**:
  - **Stream-Log mit SNI+Backend auf 443** (ursprünglich Punkt A10):
    `log_format stream_route` in `templates/nginx.conf.j2` mit
    `$ssl_preread_server_name` + `$upstream_addr`, Ausgabe nach stdout.
  - **`--doctor --json`** (ursprünglich Punkt A2): `cli.py`
    `_headless_doctor()` mit `json.dumps([asdict(r) for r in results])`.
  - **Basic Auth mit IP-Bypass** (ursprünglich Punkt A+8): `satisfy any;` in
    `templates/nginx.conf.j2`, State-Feld `ip_bypasses_basic_auth`, Menüpunkt
    in `menus/security.py` — nur anbieten, wenn Basic Auth UND Allowlist
    schon aktiv sind.
  - CI führt die Docker-Integrationstests jetzt automatisch mit aus.
- ✅ v0.12.0 — nginx-Config-Validierung vor Neustart (`nginx -t` +
  `.bak`-Rollback), Compose-Healthcheck für teddycloud, Update-Hinweis im
  Banner, Versionszeile im Image-freshness-Check
