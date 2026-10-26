# TODO — Kandidaten

Stand: 2026-10-26, v0.20.0. Offene Punkte oben, erledigte unten zur Historie.
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
