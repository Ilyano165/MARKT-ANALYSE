# Umsetzungsplan – Yellotools Market Intelligence

Stand: 2026-10-18 · Phase 1

Grundsätze: kleine, einzeln abnehmbare Meilensteine; jeder Meilenstein endet mit grünem CI,
dokumentierten Tests und einer kurzen Vorführung. Kein Meilenstein beginnt, bevor der vorige
abgenommen ist, außer wo „parallel möglich“ steht. Zeitangaben sind grobe Schätzungen in
Personentagen (PT) für eine Entwicklerin/einen Entwickler mit KI-Unterstützung und werden nach
M3 neu kalibriert.

## 1. Offene Fragen an Yellotools (vor bzw. während M0–M2 klären)

| # | Frage | benötigt für |
|---|---|---|
| Q1 | Wie viele Nutzer, welche Rollen? Reicht zunächst ein Einzelplatz-Pilot? | M6, M15 |
| Q2 | Infrastruktur: interner Server/VM, Cloud (Region EU), Docker möglich? Anmeldung über Microsoft Entra ID? | M15 |
| Q3 | Monatliches Budget für Datenanbieter, Such-API, LLM? | M2, M7, M8 |
| Q4 | Darf ein LLM (Anthropic, Verarbeitung öffentlicher Dokumente) genutzt werden? | M8 |
| Q5 | Export des Artikelstamms (Format, Felder) und Ansprechpartner Vertrieb für Produktkategorien/Anwendungsfälle | M10 |
| Q6 | Liste bestehender Kunden (nur Firmenname/Land/Branche) für Ausschluss und Scoring-Validierung? | M12 |
| Q7 | Priorisierte Zielländer und Zielbranchen für den Pilot | M7, M9 |
| Q8 | Datenschutzbeauftragte/r und Rechtsberatung für Quellenfreigaben | Gate L |
| Q9 | Soll North Data (oder ein anderer Anbieter) getestet werden? | M7 |

## 2. Entscheidungs-Gates

- **Gate F (Freigabe Phase 1):** diese Dokumente abgenommen → Beginn M0.
- **Gate L (Recht):** vor Aktivierung jeder Quelle `legal_status=approved` im Register, mit Name
  und Datum der Prüfung. Gilt besonders für Webseiten-Crawling (M9), BA Jobsuche (M11),
  Such-API-Speicherung (M9), Datenanbieter (M7).
- **Gate K (Kosten):** vor M8/M9 Kostenmessung auf 20 Beispielfirmen, Freigabe des Budgets.
- **Gate P (Pilot):** nach M13 Pilot mit realen Nutzern, danach Teambetrieb.

## 3. Meilensteine

### M0 – Projektgerüst und Qualitätswerkzeuge (≈ 3 PT)

Inhalt: `pyproject.toml` (Python ≥ 3.12), `src/market_intel`, `ruff`/`mypy --strict`/`pytest`
mit Regeln aus dem Auftragsimport, `import-linter`-Verträge für die Schichten, Sperrdateien mit
Hashes, `pip-audit`, GitHub Actions (Linux) mit Matrix SQLite + PostgreSQL-Service, Pre-Commit,
CLI `mi --version`, `CHANGELOG.md`, `LICENSE` (proprietär, zu klären).

Abnahme:
- CI grün auf Push und Pull Request; Actions auf Commit-SHA gepinnt.
- `import-linter` schlägt fehl, wenn `domain` `adapters` importiert (absichtlicher Gegentest).
- `pip-audit` ohne Befund.

Tests: Rauchtest CLI; Vertragstest der Schichtregeln.

### M1 – Domain-Kern und Datenmodell v1 (≈ 8 PT)

Inhalt: Domain-Typen (Herkunftsklasse, Status, Zeitraum, Geldbetrag mit Decimal, Evidence),
SQLAlchemy-Core-Tabellen für §3.1–3.20 aus `DATA_MODEL.md`, Alembic-Erstmigration, Repositories,
append-only-Trigger (PostgreSQL), Sicherung vor Migration, Sicht `v_current_metric`.

Abnahme:
- Migration läuft auf leerer SQLite- und PostgreSQL-Datenbank; `downgrade` nicht nötig, aber
  Sicherung vor Migration nachgewiesen.
- `UPDATE` einer Beobachtung schlägt in PostgreSQL fehl; Repository bietet kein Update.
- Gültiger-Wert-Ableitung entspricht den Regeln in `DATA_MODEL.md` §6.

Tests: Unit (Wertobjekte, Zeitraumlogik, Decimal-Rundung), Integration gegen beide DBs,
tabellengetriebene Tests der Ableitungsregeln (≥ 20 Fälle inkl. Konflikt, Supersede, Nutzerentscheid),
Property-Tests (Hypothesis) für Geldbeträge/Zeiträume.

### M2 – FetchGateway und Quellenregister (≈ 8 PT)

Inhalt: `httpx`-Gateway mit Quellenfreigabe, SSRF-Schutz, robots.txt, TDM-Vorbehalt-Erkennung,
Budget, Token-Bucket (DB), Cache mit ETag/Last-Modified, Zusammenlegen identischer Anfragen,
Wiederholung mit Backoff + `Retry-After`, Circuit Breaker, Größen-/Zeitlimits, Speicherung als
`source_document`, Kostenbuch.

Abnahme:
- Anfrage an gesperrte Quelle → `LegalBlocked`, keine Netzwerkverbindung (nachgewiesen per
  Test-Server-Zähler).
- Zweite identische Anfrage innerhalb der Frische → 0 Netzwerkzugriffe.
- Budget 1,00 € bei 0,10 €/Aufruf → genau 10 Aufrufe, der 11. wird vor dem Senden abgelehnt.
- 429 mit `Retry-After: 2` → nächster Versuch frühestens nach 2 s.
- SSRF-Suite vollständig grün.

Tests: lokaler Test-HTTP-Server (Fehlercodes, Weiterleitungen, Zeitüberschreitung,
übergroße Antwort, robots.txt-Varianten), SSRF-Korpus, Nebenläufigkeitstest Rate Limit mit 2
Workern.

### M3 – Jobsystem: Runs, Aufgaben, Worker (≈ 8 PT)

Inhalt: `research_run`/`research_task`, Lease + Heartbeat, Abhängigkeiten, Idempotenzschlüssel,
Pause/Fortsetzen/Abbruch kooperativ, Wiederaufnahme nach Absturz, Scheduler, CLI zum Starten
des Workers, Statusabfrage.

Abnahme:
- Worker-Prozess wird während einer Aufgabe hart beendet (`SIGKILL`) → nach Lease-Ablauf
  übernimmt neuer Worker, Ergebnis genau einmal gespeichert.
- Pause während laufender Aufgaben → keine neuen externen Aufrufe nach Abschluss der laufenden
  Aufrufe; Fortsetzen setzt an derselben Stelle fort.
- Budget erreicht → Run `paused` mit Grund `budget_exhausted`.
- Quelle fällt aus → Run endet `completed_with_gaps`, Lücken aufgelistet.

Tests: Integrationstests mit Fake-Adaptern, Chaos-Test (zufälliges Beenden), PostgreSQL
`SKIP LOCKED` mit 3 parallelen Workern ohne Doppelbearbeitung.

### M4 – Erste Adapter: GLEIF, Wikidata, EZB-Kurse (≈ 7 PT) · Gate L für diese drei

Inhalt: Adapter mit Identifikatoren, rechtlichem Namen, Sitz, Mutterbeziehungen (GLEIF L2),
Wikidata-QID/Website/Branche, EZB-Referenzkurse; Fixtures; Live-Smoke-Tests (manuell).

Abnahme:
- Für 20 vorgegebene Testfirmen (Liste in `tests/fixtures/companies_m4.csv`, von Yellotools
  bestätigt) werden LEI (falls vorhanden) und Mutter korrekt gespeichert; jede Angabe mit Quelle.
- Firmen ohne LEI erhalten `data_gap(reason=not_found)` statt Fehler.

Tests: Vertragstests mit aufgezeichneten Antworten inkl. Fehlerfällen; Mapping-Tests je Feld.

### M5 – Entity Resolution v1 (≈ 8 PT)

Inhalt: Normalisierung, Rechtsformlisten (DE, AT, CH, UK, US, NL, FR, IT, PL, ES), Blocking,
Merkmalsvektor, Schwellen, Prüfliste, Merge/Unmerge mit Protokoll, Markenaliase.

Abnahme:
- Goldstandard ≥ 300 handgeprüfte Paare (Duplikate, Mutter/Tochter, Namensvettern, Marken):
  **0 falsche automatische Merges**; Kandidatenliste erfasst ≥ 90 % der echten Duplikate.
- Unmerge stellt alle Beobachtungen, Aliase und Identifikatoren korrekt wieder her.

Tests: Goldstandard als Regressionstest; Property-Test „Merge → Unmerge = Ausgangszustand“.

### M6 – Web-Oberfläche v1 (≈ 10 PT) · parallel zu M5 möglich

Inhalt: FastAPI, Templates, HTMX, lokale Anmeldung (Einzelplatz), Firmensuche, Firmenprofil mit
Beobachtungshistorie und Quellen, Kennzahl-Detail, Run anlegen/steuern, Prüfliste,
Quellenregister (lesen), Fehlermeldungen im Vier-Teile-Schema.

Abnahme:
- Jeder angezeigte Wert führt in ≤ 2 Klicks zu Quelle und Zitat (NFR-05).
- Unbekannte und geschätzte Werte eindeutig gekennzeichnet.
- Lange Runs blockieren die Oberfläche nicht (Seitenaufbau < 1 s während 3 laufender Runs).
- Sicherheits-Header gesetzt; Bindung nur an 127.0.0.1.

Tests: Playwright-E2E (Chromium, vorinstalliert) für Suche → Profil → Quelle, Run
anlegen → pausieren → fortsetzen; XSS-Korpus; CSRF-Test; Host-Header-Test.

### M7 – Strukturierte Kennzahlenquellen (≈ 10 PT) · Gate L, Gate K

Inhalt: SEC EDGAR companyfacts, UK Companies House, Destatis GENESIS, Eurostat SBS; Bewertung
und – falls freigegeben – Adapter für North Data; Ableitungen (YoY, CAGR, Währung) als `derived`
mit Formel; Konfliktprüfung mit Toleranzen.

Abnahme:
- Für 10 US- und 10 UK-Testfirmen stimmen Umsatz und Mitarbeiter der letzten 3 Geschäftsjahre
  mit den Originaldokumenten überein (manueller Abgleich, Protokoll im Repo).
- Wachstumsrate wird nicht berechnet, wenn Scope oder Zeitraumlänge abweichen (Test).
- Branchenstatistik je WZ/NACE und Land abrufbar, mit Kennzeichnung NACE Rev. 2 vs. 2.1.

Tests: Vertragstests; Tabellen-Tests für Ableitungen; Konflikt-Szenarien.

### M8 – Dokumentextraktion (≈ 12 PT) · Gate K, Q4

Inhalt: PDF-/HTML-Textextraktion im Unterprozess mit Limits, Tabellenerkennung, Regel-Extraktoren
(Umsatz, Mitarbeiter, Standorte, Länder), Zitatprüfer, optionaler LLM-Extraktor (Anthropic SDK,
strukturierte Ausgabe, Batch-API für Massenläufe, Prompt-Caching für das feste Schema),
Prompt-Injection-Schutz, Kostenmessung je Dokument.

Abnahme:
- Eval-Satz ≥ 50 echte Dokumente (Geschäftsberichte, Webseiten) mit handmarkierten Werten:
  **0 gespeicherte Werte ohne verifiziertes Zitat**; Präzision der gespeicherten Werte ≥ 95 %;
  Trefferquote wird berichtet (Ziel ≥ 70 % regelbasiert + LLM).
- Prompt-Injection-Korpus: keine Abweichung vom Schema, keine unbelegten Werte.
- Kosten je Dokument gemessen und dokumentiert; Entscheidung über Modell und Einsatz durch Yellotools.

Tests: Eval-Suite (separater CI-Job, nur mit Freigabe wegen Kosten), Unit-Tests Zitatprüfer
(Normalisierung, Tausendertrennzeichen, „Mio.“), Sicherheitstests bösartige PDFs.

### M9 – Discovery: Such-API und Webseiten-Crawler (≈ 8 PT) · Gate L

Inhalt: `SearchPort` mit Brave-Adapter (alternativ Anthropic Web Search), Crawler je Domain
(robots, Tiefe ≤ 3, max. 50 Seiten, Impressum/Standorte/Karriere/Presse/IR priorisiert),
Impressum-Parser für Registernummer und USt-IdNr. (→ starke Identifikatoren).

Abnahme:
- robots.txt-Verbot und TDM-Vorbehalt werden in Tests respektiert (0 Abrufe gesperrter Pfade).
- Für 30 DE-Testfirmen wird die Registernummer aus dem Impressum korrekt erkannt (≥ 90 %), sonst
  Lücke.
- Suchergebnisse werden nur als Leads (URL, Titel, Zeit) gespeichert.

Tests: lokaler Test-Webserver mit Sites unterschiedlicher robots-Regeln; Impressum-Korpus.

### M10 – Produktkatalog und Anwendungsfälle (≈ 5 PT) · Q5 · parallel ab M6 möglich

Inhalt: Import Artikelstamm (CSV/XLSX, Spaltenzuordnung wie Auftragsimport-Katalog),
Produktkategorien, Anwendungsfälle mit Suchbegriffen und NACE-Zuordnung, Freigabe-Workflow,
Regeln für `company_product_fit`.

Abnahme:
- Import des echten Artikelstamms ohne Datenverlust (Zeilenzahl, Prüfsumme protokolliert).
- Produktfit `strong`/`plausible` nur mit Beleg (Test).
- Vertrieb hat Kategorien und mindestens 5 Anwendungsfälle freigegeben.

### M11 – Signale: Stellen, Ausschreibungen, Ereignisse (≈ 8 PT) · Gate L

Inhalt: Karriereseiten-Extraktion (aggregiert, ohne Personen), TED-Adapter (CPV-Filter je
Anwendungsfall), Signale aus Pressemitteilungen auf Firmenwebseiten, Layoff-/Insolvenzsignale,
Dublettenerkennung von Signalen. BA Jobsuche nur bei schriftlicher Klärung.

Abnahme:
- Keine Personendaten in gespeicherten Stellen- und Signaldaten (automatischer Scan im Test).
- TED: für 3 definierte CPV-Codes werden Zuschläge der letzten 12 Monate den richtigen Firmen
  zugeordnet oder als Kandidat gemeldet.

### M12 – Scoring und Chancenliste (≈ 6 PT) · Q6

Inhalt: Scoring-Modell v1 (`PRODUCT_SPECIFICATION.md` §6), Versionierung, Zerlegung in der
Oberfläche, A/B/C/insufficient_data, Rückblicktest mit Bestandskunden.

Abnahme:
- Jede Bewertung reproduzierbar aus gespeicherten Komponenten und Modellversion (Test).
- Rückblicktest: Bestandskunden liegen im Mittel signifikant über Zufallsfirmen gleicher
  Branche (Bericht; Schwellwert mit Vertrieb festlegen).
- Fehlende Indikatoren → Renormierung + Vollständigkeit, nie 0-Füllung (Test).

### M13 – Analytics und Exporte (≈ 6 PT)

Inhalt: Zeitreihen-Diagramme, Vergleichsansicht, Lücken-/Unsicherheitsübersicht, Exporte CSV/
XLSX/JSON mit Quellenspalten, Exportprotokoll.

Abnahme:
- Export enthält zu jeder Zahl Zeitraum, Status, Herkunftsklasse, Quelle-URL, Abrufdatum.
- XLSX ohne Formelinjektion (Zellen mit `=`, `+`, `-`, `@` werden escaped – Test).

→ **Gate P: Pilot** mit 1–3 Nutzern, 4 Wochen, Feedback-Liste.

### M14 – Watchlists und Änderungserkennung (≈ 5 PT)

Inhalt: Watchlists, Zeitpläne, Änderungsregeln, Benachrichtigungen in der App (E-Mail optional).

Abnahme: geänderter Wert in Fixture → genau ein `change_event` und eine Benachrichtigung; keine
Benachrichtigung bei bloßer Bestätigung eines Werts.

### M15 – Teambetrieb (≈ 8 PT) · Q1, Q2

Inhalt: PostgreSQL-Betrieb, Container-Images, `docker compose`, Reverse Proxy/TLS, OIDC,
Rollen, Audit-Log-Ansicht, Backup/Restore, Verzeichnis von Verarbeitungstätigkeiten (Entwurf).

Abnahme: Rollen-Testmatrix grün; Wiederherstellung aus Backup auf leerem System erfolgreich
(protokolliert); Penetrationstest-Checkliste (OWASP ASVS Level 1) abgearbeitet.

### M16 – Betrieb und Updates (≈ 4 PT)

Inhalt: Versionierung, signierte Images, Update-Ablauf mit Migration, Betriebsdokumentation,
Monitoring-Seite (Quellenstatus, Kosten, Fehlerquoten).

Abnahme: Update von Version n auf n+1 mit Migration auf Kopie der Produktivdaten erfolgreich.

## 4. Übersicht

| MS | Inhalt | PT (grob) | Abhängig von |
|---|---|---|---|
| M0 | Gerüst, CI | 3 | Gate F |
| M1 | Domain, DB | 8 | M0 |
| M2 | FetchGateway | 8 | M1 |
| M3 | Jobsystem | 8 | M1 |
| M4 | GLEIF/Wikidata/EZB | 7 | M2, M3, Gate L |
| M5 | Entity Resolution | 8 | M4 |
| M6 | Web-UI v1 | 10 | M3 (parallel M5) |
| M7 | Strukturierte Kennzahlen | 10 | M5, Gate L/K |
| M8 | Extraktion | 12 | M7, Gate K, Q4 |
| M9 | Discovery/Crawler | 8 | M5, Gate L |
| M10 | Produktkatalog | 5 | M6, Q5 |
| M11 | Signale | 8 | M9 |
| M12 | Scoring | 6 | M10, M11 |
| M13 | Analytics/Export | 6 | M12 → Gate P |
| M14 | Watchlists | 5 | M13 |
| M15 | Teambetrieb | 8 | Q1, Q2 |
| M16 | Betrieb/Updates | 4 | M15 |
| | **Summe** | **≈ 124 PT** | |

## 5. Teststrategie (übergreifend)

| Ebene | Werkzeug | Läuft |
|---|---|---|
| Unit | pytest, Hypothesis | jeder Push |
| Integration DB | pytest gegen SQLite und PostgreSQL-Service | jeder Push |
| Vertragstests Quellen | aufgezeichnete Antworten (Fixtures), keine Netzverbindung | jeder Push |
| E2E | Playwright (Chromium) | jeder Push (Kernpfade), nächtlich vollständig |
| Sicherheit | SSRF-, XSS-, Dokument-, Prompt-Injection-Korpora | jeder Push |
| Eval Extraktion/Resolution | Goldstandard-Datensätze | manuell/nächtlich, Kosten freigegeben |
| Live-Smoke | echte Quellen, minimal | manuell, nie im Standard-CI |

Testdaten mit echten Firmendaten bleiben auf öffentliche Unternehmensangaben beschränkt; keine
Kundendaten von Yellotools im Repository (Lehre aus `248090.xml` im Auftragsimport).

## 6. Nächster konkreter Schritt nach Freigabe

M0 anlegen: Gerüst, Werkzeuge, CI, Schichtverträge. Danach M1 mit Datenmodell v1. Bis zur
Abnahme von M3 wird keine externe Quelle live angebunden.
