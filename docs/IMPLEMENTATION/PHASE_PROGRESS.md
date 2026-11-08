# Entwicklungsphase 1 – Fortschritt

Grundlage: `docs/audits/IC_WARE_FORENSIC_AUDIT.md` (Stand 2026-11-06).
Stand dieses Dokuments: 2026-11-08, Version 0.6.0, Branch `claude/confident-faraday-0tsww5`.

Kennzeichnung: **VERIFIZIERT** = in diesem Projekt tatsächlich ausgeführt und Ergebnis
gesehen. **NICHT VERIFIZIERT** = nicht ausführbar oder nicht geprüft (mit Grund).

## Überblick

| Phase | Ziel | Commit | Ergebnis |
|---|---|---|---|
| A | Speichern im Browser (P0) | 64384e3 | VERIFIZIERT in Chromium |
| B | Ein Bewertungsstand überall (P1-4) | a1925f6 | VERIFIZIERT mit Audit-Datenbank |
| C | Rechercheaufträge + Hintergrund-Worker (P1-1, P1-5) | 0da31d4 | VERIFIZIERT (Test-Internet und echte Quellen) |
| D | TED-Rollen, Datenpflege, Import (P1-2, P1-3 teilweise) | c15ca5f | VERIFIZIERT |
| E | Desktop-Anwendung mit eigenem Fenster (P2) | 0640387, b051df0, 8aa309c, 3207cb5 | VERIFIZIERT auf Windows (CI) |
| F | Backup, Logs, Wettbewerb, Dubletten, Navigation | 3f5e4d0, f3ff17b, b051df0 | VERIFIZIERT (lokal) |

Tests lokal (Linux, Python 3.13): **310 bestanden, 3 übersprungen** (zwei manuelle Live-Tests,
ein Windows-Test). Windows (GitHub Actions, Python 3.12, Lauf 12): **311 bestanden,
2 übersprungen** (die zwei manuellen Live-Tests). `ruff` und `mypy --strict` ohne Befund.

---

## Phase A – Speichern im Browser

**Ursache:** `Referrer-Policy: no-referrer` lässt Chromium/Edge bei Formularen
`Origin: null` senden; die Herkunftsprüfung lehnte das mit 403 ab.

**Änderungen:**
- `Referrer-Policy: same-origin` (externe Quellenlinks bekommen weiterhin keinen Referrer).
- Neue Prüfung `origin_allowed()`: `Sec-Fetch-Site` nur same-origin/none, `Origin` exakt
  `http://<erlaubter Host>`, `null` nur mit bestätigter gleicher Herkunft, `Referer` als
  Rückfall, CSRF-Token bleibt Pflicht.

**Dateien:** `src/market_intel/ui/server.py`, `tests/test_browser.py`, `pyproject.toml`.

**Tests (VERIFIZIERT):**
- 17 Regelfälle.
- Chromium: Notiz speichern und entfernen, Watchlist, Vertriebsergebnis, Wettbewerbseinordnung.
- Cross-Origin-Formular mit gültigem Token → 403.
- POST ohne Token → 403.
- Gegenprobe: Mit `no-referrer` schlagen die vier Browser-Tests fehl.

## Phase B – Bewertungskonsistenz

**Änderungen:**
- Jede Bewertungsrunde speichert ihre Grundlage (`details.basis`):
  - Modellversion,
  - Prüfsumme von Produktmatrix, Anwendungsfällen, Rollenregeln und Modell,
  - Datenstand je Firmentabelle als Fingerabdruck.
- `freshness()` erkennt „veraltet“ (Modell, Regeln, Daten) und „nicht bewertet“.
- Lead-Liste, Opportunity-Seite und Export lesen dieselbe gespeicherte Runde.
- Die Opportunity-Seite bewertet eine veraltete Firma vor der Anzeige neu und speichert sie.
- Veraltete Bewertungen werden markiert und hinten einsortiert.
- Neubewertung per Knopf (bei laufendem Worker als Auftrag) und mit `mi reassess`.
- Runden werden atomar geschrieben. Ein älterer Lauf überschreibt nie einen neueren.

**Dateien:** `opportunity/current.py` (neu), `assess.py`, `run.py`, `leads.py`, `report.py`,
`cli_opportunity.py`, `ui/render.py`, `ui/server.py`.

**VERIFIZIERT mit der Audit-Datenbank (befüllte Kopie):**

| Firma | Lead-Liste vorher | Lead-Liste = Opportunity-Seite jetzt |
|---|---|---|
| Avery Dennison | Fit 81 (Modell v2), jetzt als veraltet markiert | Fit 40 / hinweis |
| ORAFOL | Fit 53 (v2) | Fit 20 / unklar |
| Umdasch | Fit 80 (v2) | Fit 58 / belegt |
| Steinel | 0 | 0 / kein_beleg |
| Deutsche Bank | 0 | 0 / kein_beleg |
| Sixt | 0 | 0 / keine_daten |

Abweichungen zwischen Lead-Liste und Opportunity-Seite: 0.

**Tests:** 9 in `tests/test_assessment_consistency.py`. Gegenproben bestätigen, dass sie ohne
Neueren-Schutz bzw. ohne Neubewertung fehlschlagen.

## Phase C – Rechercheaufträge und Hintergrund-Worker

**Änderungen:**
- `jobs.py` mit den Tabellen `job` und `job_event` (Schema 6).
- Der Worker läuft im Anwendungsprozess oder als `mi worker`.
- Übernahme atomar (kein Doppelstart), Herzschlag alle 10 s.
- Wiederaufnahme nach Absturz oder Beenden an der nächsten offenen Teilaufgabe, nach
  3 Versuchen „fehlgeschlagen“.
- Pause und Abbruch greifen vor der nächsten Teilaufgabe. Fehler beenden nur den eigenen Auftrag.
- Ehrlicher Endstatus: keine Quelle erreichbar und nichts gefunden = „fehlgeschlagen“.
- Oberfläche:
  - `/jobs/new`: Zielgruppe bzw. Anwendungsfall, Branchen, Suchbegriffe, Firmen, Regionen, Umfang.
  - `/jobs/<id>`: Live-Fortschritt aus gespeicherten Zahlen, Anhalten/Abbrechen/Fortsetzen,
    Ergebnisliste mit Bewertung, Protokoll.
- CLI: `mi research --background`, `mi worker`, `mi jobs`.

**Tests (12 in `tests/test_jobs.py`, VERIFIZIERT):**
- echte 8-Schritte-Pipeline über den Worker gegen das Test-Internet,
- alle Quellen gesperrt → fehlgeschlagen,
- Pause nach Schritt 1 und Fortsetzen,
- Absturz-Wiederaufnahme ohne Wiederholung von Schritt 1,
- 4 Worker konkurrieren um 6 Aufträge, jeder Auftrag genau einmal verarbeitet,
- fehlerhafter Auftrag blockiert nicht,
- Chromium vom Formular bis zur Ergebnisliste.

## Phase D – Recherchequalität und Datenpflege

**TED:**
- Auftraggeber (Behörden) werden nur als `public_buyer` mit Bedarfssignal gespeichert. Sie
  werden nie ausgewählt und nicht als Leads gezeigt.
- Zuschlagsempfänger (`winner-name`) sind Kandidaten, aber nur bei Zuschlagsbekanntmachungen,
  deren Titel den Suchbegriff enthält.
- Die Felder wurden am 2026-11-08 live an der TED-API geprüft. Dabei zeigte sich: Gewinnerlisten
  von Mehrlos-Ausschreibungen enthalten Fahrzeughersteller, und die Listen sind nicht
  positionsgleich. Die USt-IdNr. wird deshalb nur bei genau einem Gewinner übernommen.

**Auswirkung auf bestehende Ergebnisse (VERIFIZIERT):**
- Befüllte Audit-DB: 252 von 516 Firmen sind Auftraggeber.
- Audit-Lauf: 4 der 5 damaligen Auswahlen waren Behörden und würden nicht mehr ausgewählt.
- Echter Lauf heute: 3 von 3 Auswahlen sind Betriebe, keine Behörde (siehe E2E).

**Datenpflege (`/maintenance`, Profil-Reiter „Stammdaten pflegen“):**
- Website eintragen, ändern oder entfernen. Sie wird geprüft, nie geraten. Eine Domain, die
  schon zu einer anderen Firma gehört, löst einen Hinweis aus statt eines Überschreibens.
- Name und Land korrigieren.
- Dubletten zusammenführen (Begründung Pflicht) oder als verschieden markieren.
- Änderungsprotokoll, an das nur angehängt werden kann.

**Import (`/imports`):**
- CSV-Upload für Firmenlisten oder Bestandskunden.
- Ergebnis je Zeile: neu, zugeordnet, Dublette in der Datei, übersprungen mit Grund.
- Ein erneuter Import legt keine Dubletten an.
- „Recherche für diese Liste starten“.

**Tests:** 11 in `tests/test_phase_d.py`, davon einer mit Chromium.

## Phase E – Desktop-Anwendung

**Architektur:** Die Hülle (`shell.py`) startet dasselbe Backend wie `mi ui` (Server und
Worker) im eigenen Prozess. Kein zweites Programm.

**Fenster:**
- pywebview (BSD-3) mit Edge WebView2.
- Rückfall: Edge/Chrome im App-Modus, danach der Standardbrowser.
- Es öffnet sich kein Konsolenfenster mehr. Die Kommandozeile ist das eigene Programm `mi.exe`.

**Betrieb:**
- Einzelinstanz über eine Betriebssystem-Sperre. Ein Zweitstart holt das laufende Fenster nach
  vorn (Token-geschützt).
- „Anwendung beenden“ und Schließen halten Aufträge an.
- Fehler erscheinen als Dialog, aber nur im installierten Programm.
- `/health` mit Hinweis bei unterbrochener Verbindung.
- Protokolldatei mit Rotation.

**Installer (NSIS):**
- Programmsymbol, Startmenü- und Desktop-Verknüpfung.
- Herausgeber und Größe unter Apps & Features.
- Abbruch, wenn die Anwendung noch läuft.

**VERIFIZIERT (Linux):**
- 9 Tests in `tests/test_shell.py` (Sperre, Fokus nur mit Token, Beenden, Lauf ohne Fenster,
  Zweitstart-Übergabe, Startfehler sichtbar, Log-Rotation, kein blockierender Dialog).
- Linux-PyInstaller-Build mit beiden Programmen: `mi --version` und `mi jobs` laufen.
- E2E mit der Hülle: Start, Beenden, Neustart, Zweitstart (Exitcode 0 nach 0,8 s).

**VERIFIZIERT auf Windows** (Abschnitt „Windows-Build“): Der Rauchtest öffnet das echte
WebView2-Fenster (`MarketIntel.exe --smoke-test`), liest Titel und Navigation aus dem Fenster,
prüft Zweitstart und Logdatei; Installation, Deinstallation und Abbruch bei laufender App
bestanden.

**Nicht automatisiert prüfbar:** wie das Fenster für einen Menschen aussieht und sich bedient
(Fenstergröße, Symbol in der Taskleiste). Das Programmsymbol ist in die EXE eingebettet
(PyInstaller „Copying icon to EXE“); die Darstellung in der Taskleiste ist **NICHT VERIFIZIERT**.

**Gefundener und behobener Fehler:** Der Windows-Testlauf zu Phase E hing. Ursache: Ein Test
provoziert einen Startfehler, und unter Windows öffnete `show_error` einen modalen Dialog, den
niemand schließen konnte. Jetzt erscheint der Dialog nur im installierten Programm
(`MI_NO_DIALOG=1` schaltet ihn ab). Zusätzlich bekam der Testschritt ein Zeitlimit und
faulthandler.

## Phase F – Weitere Produktqualität

**Änderungen:**
- **Datensicherung:**
  - SQLite-Online-Backup mit Integritätsprüfung, die letzten 10 bleiben erhalten.
  - Tägliche automatische Sicherung beim Start.
  - Bedienung über `mi backup create|list|restore` und die Einstellungsseite.
  - Wiederherstellung nur bei beendeter Anwendung, mit Sicherung des aktuellen Stands.
- **Logs:** `<Datenordner>/logs/market_intel.log`, 2 MB × 6.
- **Wettbewerb:**
  - Keine Auftraggeber im Vergleich.
  - Aufgelistet werden nur Firmen mit belegtem Bezug, die übrigen nur gezählt.
  - Gespeichert werden nur Einordnungen mit Bezug.
- **Dubletten in der Anzeige:**
  - Pressemeldungen nicht mehr doppelt (Signal und Ereignis).
  - TED-Meldungen einmal mit allen Anwendungsfällen.
  - Statt „Datum unbekannt“ das Veröffentlichungs- bzw. Abrufdatum.
  - **Ursache der 88 Gruppen aus dem Audit:** dieselbe Bekanntmachung, einmal je
    Anwendungsfall gespeichert. Hinzu kommen verschiedene Bekanntmachungen mit gleichem Titel;
    das sind echte, getrennte Datensätze, die korrekt einzeln bleiben.
- **Oberfläche:**
  - Seitenleiste nach Arbeitsabläufen.
  - Übersicht mit echten Kennzahlen und „Zu erledigen“.
  - Unternehmen mit Seitenaufteilung (50 je Seite).
  - Bestandskunden, Produkt-Fit, Quellen & Belege, Einstellungen.
  - Deutsche Bezeichnungen statt interner Codes.
  - Designsystem mit Tokens (hell/dunkel, mobil, ohne horizontalen Überlauf bei 390 px).
- **Visuelle Referenzen:** liegen nicht vor, ein Abgleich ist **NICHT VERIFIZIERT**. Die
  Gestaltung ist über die Tokens in `app.css` vorbereitet.

**Tests:** 8 in `tests/test_phase_f.py`. Screenshots aller Hauptseiten mit Audit-Daten,
0 JavaScript-Fehler.

---

## End-to-End-Ablauf mit echten Quellen (2026-11-08, VERIFIZIERT)

Ablauf: Desktop-Hülle mit Worker im Modus ohne Fenster (diese Umgebung hat keine grafische
Oberfläche), bedient mit Chromium. Kontaktadresse nicht gesetzt.

| # | Schritt | Ergebnis |
|---|---|---|
| 1 | Anwendung starten | ok (Health) |
| 2 | Anmelden | **entfällt**: lokale Einzelplatz-Anwendung ohne Benutzerkonten. Eine Anmeldung ist nicht implementiert und wird nicht vorgetäuscht. |
| 3–4 | Auftrag „Fahrzeugfolierer Deutschland“ anlegen und starten | ok |
| 5 | Fortschritt | Phasen beobachtet: wartet → Unternehmen werden gesucht → Quellen werden geprüft → Unternehmen werden bewertet → teilweise fehlgeschlagen |
| 6 | Unternehmen gefunden | 3 Betriebe: Check Matt Fahrzeugfolierung GmbH (GLEIF), CFC CarFilmComponents e. K. und DEKO.TEAM Inh. Laura Bonner (TED-Zuschläge) |
| 7 | Quellenbelege | vorhanden (GLEIF- bzw. TED-Dokument) |
| 8 | Bewertung | gespeichert, Modell v3; alle 3: Fit 27, „unklar“, manuelle Prüfung |
| 9–11 | Lead verwalten, Notiz, Watchlist | ok |
| 12 | Export | `leads.csv` mit 3 Zeilen |
| 13 | Anwendung schließen | über „Anwendung beenden“, Exitcode 0 |
| 14–15 | Neustart, Daten wieder aufrufen | Auftrag, Notiz, Watchlist-Status und Lead vorhanden; tägliche Sicherung angelegt |

**Abrufe:** 18, davon 9 erfolgreich und 9 gescheitert.
- Wikidata: HTTP 403 (Robot-Policy).
- GDELT: HTTP 429 (Rate-Limit).

Beide wurden protokolliert und nicht umgangen.

**Im E2E gefundene und behobene Fehler:**
- Neustart lief auf einem Zufallsport.
- Jeder Hinweis stand doppelt im Protokoll.

## Offene Probleme (ehrlich)

1. **Automatische Website-Ermittlung fehlt (Audit P1-3, weiterhin offen).**
   - Ohne Website gibt es keine eigenen Firmenbelege. Alle 3 E2E-Treffer sind deshalb nur
     „unklar / manuelle Prüfung“.
   - Websites lassen sich jetzt manuell pflegen und direkt aus der Ergebnisliste eintragen.
   - Eine automatische Ermittlung mit Bestätigung durch den Nutzer ist der nächste wichtige
     Schritt.
2. **Wikidata blockiert diese Umgebung (403), GDELT drosselt (429).**
   - Mit gesetzter `contact_email` ist Wikidata eventuell erreichbar. **NICHT VERIFIZIERT.**
3. **Anmeldung/Benutzerkonten:** nicht vorhanden. Die Anwendung ist eine lokale
   Einzelplatz-Anwendung.
4. **Der Installer ist unsigniert.** Ein Code-Signing-Zertifikat kostet Geld, SmartScreen
   warnt beim ersten Start.
5. **Bereitstellung nur als Actions-Artefakt** (30 Tage, GitHub-Anmeldung nötig). Ein
   dauerhafter Release-Download ist nicht eingerichtet.
6. **Visuelle Referenzen** liegen nicht vor, ein Abgleich ist offen.
7. **Die Produktmatrix ist „NICHT verifiziert“**, also recherchiert, aber nicht vom Hersteller
   bestätigt (unverändert).
8. **Wiederherstellung einer Sicherung nur per Kommandozeile**, mit Absicht: nur bei beendeter
   Anwendung sicher.

## Windows-Build (VERIFIZIERT, GitHub Actions `windows-latest`, Windows Server 2025)

Lauf 12 zu Commit 3207cb5 – alle Schritte grün:
<https://github.com/Ilyano165/MARKT-ANALYSE/actions/runs/42497233814>

| Schritt | Ergebnis |
|---|---|
| Tests unter Windows (mit Chromium) | 311 bestanden, 2 übersprungen (3:37 min) |
| PyInstaller: `MarketIntel.exe` (Fenster) + `mi.exe` (Kommandozeile) | ok |
| Rauchtest Paket | `mi` (Version, Produktmatrix, CSV-Roundtrip, `mi jobs`), Server-Seiten, **echtes Fenster:** `{"mode": "webview", "title": "Übersicht · Market Intelligence", "nav": 10, "second_instance_focus": true}`, Logdatei |
| NSIS-Installer | `MarketIntel-Setup-0.6.0.exe` (34,3 MB) |
| Installieren, testen, deinstallieren | Startmenü- und Desktop-Verknüpfung, Apps & Features, Fenstertest im installierten Programm, Deinstallation bricht bei laufender App ab (Exitcode ≠ 0, Dateien bleiben), danach vollständige Deinstallation |

Verlauf der Fehler auf dem Weg dorthin (alle behoben):
- Lauf 7–9 hingen im Testschritt: modaler Fehlerdialog im Test (siehe Phase E).
- Lauf 10: Rauchtest prüfte noch den alten Titel; das Fenster selbst funktionierte bereits.
- Lauf 11: Installer erkannte die laufende Anwendung nicht (Umbenennen laufender Programme ist
  unter Windows erlaubt); jetzt Schreibprobe.

## Installer

- **Artefakt:** `MarketIntel-Setup` aus Lauf 12, Datei `MarketIntel-Setup-0.6.0.exe`.
  Download (GitHub-Anmeldung, 30 Tage gültig bis 2026-12-08):
  <https://github.com/Ilyano165/MARKT-ANALYSE/actions/runs/42497233814/artifacts/13623653042>
- **Pfad im Build:** `dist-installer/MarketIntel-Setup-0.6.0.exe` (Repository-Wurzel).
- **Installiert nach:** `%LOCALAPPDATA%\Programs\Yellotools Market Intelligence\` mit
  `MarketIntel.exe` und `mi.exe`.
- **Daten:** `%LOCALAPPDATA%\YellotoolsMarketIntel\`, darin `market_intel.db`, `logs\`,
  `backups\` und `out\`.
