# Entwicklungsphase 1 – Fortschritt

Grundlage: `docs/audits/IC_WARE_FORENSIC_AUDIT.md` (Stand 2026-11-06).
Stand dieses Dokuments: 2026-11-12, Version 0.7.0, Branch `claude/confident-faraday-0tsww5`.

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
| G | Automatische Website-Ermittlung mit Bestätigung (P1-3) | df5463c | VERIFIZIERT (lokal + echter E2E), Windows siehe unten |

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

## Phase G – Automatische Website-Ermittlung (Audit P1-3)

**Ziel:** Für gefundene Unternehmen mögliche offizielle Websites automatisch ermitteln, jeden
Kandidaten nachvollziehbar prüfen und dem Nutzer zur Entscheidung vorlegen. Die Funktion ist
Teil der bestehenden Anwendung: dieselbe Datenbank, derselbe Abruf über das `FetchGateway`
(robots.txt, Drosselung, Cache, `fetch_log`) und dieselben Aufträge und Worker. Es gibt keinen
zweiten Server.

### Funktionen

1. **Kandidatenquellen.** Es werden nur Domains geprüft, die eine Quelle tatsächlich nennt oder
   die sich nachvollziehbar aus dem Namen ableiten:
   - **Bestand:** die bereits gespeicherte Website, mit Herkunft (`company.website_source`).
   - **TED-Bieterkontakt:** E-Mail-Domain aus `organisation-email-tenderer`. Sie wird nur
     genutzt, wenn genau ein Bieter genannt ist und dieser der Zuschlagsempfänger ist.
     Freemail-Domains (gmail, web.de, …) werden ignoriert. Adresse und Telefon werden
     zusätzlich als belegte Vergleichsdaten gespeichert.
   - **Wikidata P856:** nur bei passendem Namen und gleichem Land.
   - **Ableitung aus dem Namen:** höchstens 4 Domains, z. B. `check-matt.de`. Sie gelten nie
     als Beleg, nur als Prüfkandidat, und werden mit einem einzigen Abrufversuch geprüft.
2. **Prüfung jedes Kandidaten** über Startseite, Weiterleitungen sowie Impressum und Kontakt
   (höchstens 2 Unterseiten). Der Matching-Score enthält nur belegte Treffer:
   - Name: +35 / +18 / +8, fremder Firmenname −30
   - Registernummer: +30, abweichend −25
   - Ort und PLZ: +20 (+5 mit Straße), nur Ort +8, anderer Ort −20
   - Telefon und E-Mail-Domain: je +10
   - Branchenbegriffe: +5
   - Domain entspricht dem Namen: +5 / +2
   - von einer unabhängigen Quelle genannt: +10

   Fehlende Angaben zählen nie positiv; sie erscheinen als „nicht geprüft“. Die Urteile lauten
   eindeutig, wahrscheinlich, unklar, widerspricht und nicht erreichbar. Die vollständige
   Tabelle steht in `PRODUCT_SPECIFICATION.md` §6c.
3. **Keine Zuordnung über Namensähnlichkeit allein.** Abgeleitete Domains, die nicht
   erreichbar, unklar oder widersprüchlich sind, werden nicht vorgeschlagen. Eine Weiterleitung
   auf eine fremde Firma (im E2E: `deko-team.de`) wird erkannt.
4. **Entscheidungen** im Reiter „Website-Ermittlung“ des Unternehmensprofils, in der Sammelansicht
   `/websites` und mit `mi websites`:
   - bestätigen (auch „trotzdem bestätigen“ bei nicht erreichbarer Seite)
   - eine andere wählen
   - ablehnen mit Grund
   - manuell eintragen
   - erneut suchen

   Jede Entscheidung wird mit Autor und Zeit in `company_edit` protokolliert.
5. **Abgelehnte Vorschläge** tauchen nicht wieder auf, solange sich die Suchgrundlage nicht
   ändert. Die Suchgrundlage ist ein Hash aus Name, Land, Orten und PLZ.
6. **Integration in Rechercheaufträge:** Schritt 3 heißt jetzt „Quellen werden geprüft, Websites
   ermittelt“. Der Fortschritt zeigt bestätigt, aus Quelle, Vorschlag, zu prüfen und keine.
   Prüfabrufe werden getrennt von Quellenfehlern gezählt. Nicht erreichbare Quellen blockieren
   den Auftrag nicht.
7. **Nach der Bestätigung** folgt ein Auftrag „Website-Analyse“ mit diesen Schritten:
   - Abruf der Website
   - Extraktion mit Belegen
   - Neubewertung nach den Regeln aus Phase B, mit dem Anlass `website-analyse`

   Ohne Website gibt es keine Abwertung. Das Unternehmen erscheint als „unvollständige
   Datenlage“, und Lead-Liste und Export zeigen eine Spalte `website_status`.
8. **Sammelansicht** pro Auftrag mit „Alle N eindeutigen Vorschläge bestätigen“. Diese Aktion
   bestätigt nur das Urteil „eindeutig“ und nur, wenn es genau einen solchen Kandidaten pro
   Firma gibt.

### Datenbank (Schema 6 → 7, automatische Migration)

- Neue Tabelle `website_candidate` (eindeutig je Firma und Domain) mit:
  - Status und Urteil
  - Score und Quellen
  - Prüfdetails und Dokument-IDs
  - Suchgrundlage
  - Entscheidung mit Autor, Zeitpunkt und Grund
- Neue Tabelle `website_search` für das Protokoll jedes Suchlaufs mit Ergebnis.
- Neue Spalte `company.website_source` mit den Werten:
  - `manuell`
  - `bestaetigt`
  - `quelle:liste`
  - `quelle:wikidata`
  - `quelle:unbekannt`
- Neue Kennungen `email_domain` und `phone` sowie der Standorttyp `other_site` aus
  TED-Bieterkontakten.

### Geänderte Dateien

- **Neu:**
  - `src/market_intel/websites.py`
  - `tests/test_websites.py`
- **Geändert:**
  - `db.py`, `repo.py`, `jobs.py`, `research.py`, `maintenance.py`
  - `http/gateway.py`
  - `sources/ted.py`, `sources/registry.py`, `sources/user_data.py`, `sources/wikidata.py`
  - `opportunity/current.py`, `opportunity/evidence.py`, `opportunity/leads.py`
  - `cli_opportunity.py`, `config.py`, `__init__.py`
  - `ui/server.py`, `ui/views.py`, `ui/render.py`, `ui/static/app.css`, `ui/static/app.js`
  - `tests/test_assessment_consistency.py`
- **Dokumentation:**
  - `README.md`
  - `RESEARCH_SOURCES.md` §3a
  - `PRODUCT_SPECIFICATION.md` §6c
  - `DATA_MODEL.md` §3.29

### Quellen und Grenzen (ehrlich)

| Quelle | Status in dieser Umgebung (2026-11-12) |
|---|---|
| TED-Bieterkontakt | VERIFIZIERT, liefert E-Mail-Domain, Adresse und Telefon |
| GLEIF | VERIFIZIERT, liefert Adresse und HRB-Nummer als Vergleichsdaten |
| Wikidata | HTTP 403/429 aus dieser Umgebung (geteilte IP). Wird protokolliert, nicht umgangen. Unter Windows bzw. beim Nutzer NICHT VERIFIZIERT. |
| Nominatim (OSM) | **ausgeschlossen:** robots.txt verbietet `/search` |
| Overpass (OSM) | nicht erreichbar (Zeitüberschreitung), daher nicht eingebaut |
| Allgemeine Suchmaschinen | nicht eingebaut: keine kostenlose, erlaubte Such-API |

- Vorschläge brauchen immer eine Bestätigung durch einen Menschen. Ein hoher Score ist ein
  Hinweis, kein Beweis.
- Für Firmen, die weder in TED/Wikidata eine Website haben noch unter einer naheliegenden Domain
  erreichbar sind, findet die Suche ehrlich nichts.

### Im Zuge von Phase G gefundene und behobene Fehler

1. **Schwer (Phase B):** `rules_digest` hing vom Hash-Seed des Prozesses ab, weil
   `repr(frozenset)` je nach Prozess anders ausfällt. Nach jedem Neustart galten deshalb alle
   Bewertungen als „veraltet – Regeln geändert“.
   - Jetzt kanonisches JSON.
   - Regressionstest über 4 Prozesse mit verschiedenem `PYTHONHASHSEED`. Er schlägt ohne den
     Fix fehl.
   - Folge für bestehende Installationen: Beim ersten Start nach dem Update werden die
     Bewertungen einmalig als veraltet angezeigt. Danach bleiben sie stabil.
2. **Leistung:** Die Opportunity-Seite brauchte bei einer Firma mit analysierter Website
   103 s, im Browser lief sie in einen Timeout.
   - Ursache: Satztrennung und Kleinschreibung wurden für jeden Suchbegriff neu berechnet.
   - Mit Zwischenspeicher dauert sie 0,94 s, mit identischen Ergebnissen.
3. **Fehlerzahlen:** Geratene Domains wurden mit 3 Wiederholungen abgerufen. Dadurch stieg
   „Quellen fehlgeschlagen“ auf 124.
   - Abgeleitete Domains werden jetzt nur einmal versucht.
   - Nicht erreichbare Hosts werden für Firmen-Websites gemerkt.
   - Prüfabrufe werden getrennt gezählt. Ergebnis: 20 Quellenfehler, 67 Prüfabrufe.

### Echter End-to-End-Test (2026-11-12, VERIFIZIERT)

Ablauf: Desktop-Hülle im Browser-Modus und Chromium (Playwright). Die Datenbank war leer. Die
Quellen waren echt, ohne Attrappen. Auftrag: „Finde Fahrzeugfolierer in Deutschland“, 8
Kandidaten, 400 Abrufe, 15 Minuten.

| Schritt | Ergebnis |
|---|---|
| Rechercheauftrag | 3 Firmen; 23 Quellen geprüft, davon 8 ok und 15 fehlgeschlagen (protokolliert); 67 Website-Prüfabrufe. Status „teilweise fehlgeschlagen“, nicht blockiert. |
| Vorschlag CFC CarFilmComponents e. K. | `cfc.de`, Score 78, „wahrscheinlich“: PLZ 82398 und Ort, Telefonnummer und E-Mail-Domain wie im TED-Bieterkontakt, Begriffe „car wrapping“ und „fahrzeugbeschriftung“, unabhängige Quelle TED |
| Vorschlag Check Matt Fahrzeugfolierung GmbH | `check-matt.de`, Score 43, „wahrscheinlich“: Adresse einschließlich Straße wie bei GLEIF |
| DEKO.TEAM Inh. Laura Bonner | `deko.team` nicht erreichbar (SSL-Fehler). `deko-team.de` leitet auf eine fremde Firma weiter und wurde daher nicht vorgeschlagen. |
| Bestätigung von `cfc.de` | löst die Website-Analyse aus: 8 Seiten, 29 Profilfakten, neu bewertet |
| Opportunity-Seite | lädt; „Anlass: website-analyse · gleicher Stand wie Lead-Liste und Export“ |
| CSV-Export | CFC: Priorität 64, „belegt“, Website `cfc.de`, „Website bestätigt“, „aktuell“. Die beiden anderen: Priorität 37, „Vorschläge – manuelle Prüfung nötig“, „aktuell“. Ohne Website gibt es also keine Abwertung unter das Niveau ohne Daten. |
| Beenden und Neustart | Exitcode 0. Website-Prüfung lädt, die Bewertung ist weiterhin „website-analyse“ und **nicht** als veraltet markiert. JavaScript-Fehler: keine. |

Vorherige Läufe sind ehrlich dokumentiert:
- Lauf 1 scheiterte an der langsamen Opportunity-Seite. Der Fehler ist behoben, siehe oben.
- Lauf 2 scheiterte an einem Locator im Testskript, weil das Formular zwei Knöpfe hat. Das war
  kein Fehler der Anwendung.

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

1. **Automatische Website-Ermittlung: umgesetzt in Phase G** (siehe dort). Der folgende Stand ist überholt:
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
