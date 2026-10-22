# Architektur – Yellotools Market Intelligence

Stand: 2026-10-22 · Revision 5 (Customer Discovery, Phase 4; verbindlich: **100 % kostenloser Betrieb**) · Version 0.4.0

Dieses Dokument enthält (1) die Bestandsaufnahme, (2) die Grundsatzentscheidungen inklusive
Kostenfreiheit, (3) die Architekturentscheidungen (ADRs), (4) Komponenten und Schnittstellen,
wie sie im Prototyp **tatsächlich implementiert** sind, (5) Datenflüsse und (6) Risiken.
Quellen: `RESEARCH_SOURCES.md` · Kosten und Lizenzen: `docs/DEPENDENCIES_AND_COSTS.md` ·
Datenmodell: `DATA_MODEL.md` · Plan: `IMPLEMENTATION_ROADMAP.md` · Sicherheit:
`SECURITY_AND_PRIVACY.md`.

---

## 1. Bestandsaufnahme (unverändert gültig, Stand 2026-10-18)

| Repository | Befund |
|---|---|
| `Ilyano165/MARKT-ANALYSE` | war leer (kein Commit); enthält jetzt Planung und Prototyp |
| `Ilyano165/AUFTRAGSIMPORT2.0`, Branch `claude/focused-tesla-q81pbc` (Commit `73249a3`) | Python ≥ 3.12, PySide6-Desktop, SQLite (WAL, Migrationen über `user_version`), Herkunftsmodell `Field`/`Evidence`, Vier-Teile-Fehlermeldungen, Passwörter im Windows-Anmeldespeicher, Protokollbereinigung, WiX-MSI-Build (laut Changelog noch nie auf echtem Windows gelaufen, Signatur fehlt). Tests am 2026-10-18 ohne GUI: **734 bestanden, 21 übersprungen, 1 xfail** |
| `AUFTRAGSIMPORT2.0`, Branch `main` | nur .NET-Azure-Vorlage und ZIP-Archiv |
| `Ilyano165/YT-Import-auftrag` | Vorgänger 1.1.0, durch 2.0 abgelöst |

Übernommene Konzepte aus dem Auftragsimport (neu implementiert, keine Laufzeitabhängigkeit):
Herkunft je Wert, „nichts wird geraten“, SQLite mit WAL und Fremdschlüsseln, Geheimnisse nie in
Konfigurationsdateien (`config.py` lehnt Schlüssel wie `api_key` ab), strenge Werkzeuge
(`ruff`, `mypy --strict`, `pytest`).

## 2. Grundsatzentscheidungen

### 2.1 Eigenständige Anwendung (unverändert)

Market Intelligence ist eine eigene Anwendung ohne gemeinsame Datenbank, Prozesse oder
Codeabhängigkeit zum Auftragsimport. Hauptgrund: Der Auftragsimport sichert zu, keine Daten an
Dritte zu übertragen und keine KI-Dienste zu nutzen; Market Intelligence lebt von ausgehenden
Internetabrufen. Weitere Gründe: anderes Betriebsmodell (lange Hintergrundläufe), offenes
Release-Risiko des Auftragsimports, keine fachliche Überschneidung außer dem Artikelstamm
(Dateiimport, Meilenstein M10).

### 2.2 Verbindlich: 100 % kostenloser Betrieb

| Regel | Umsetzung im Code |
|---|---|
| Keine kostenpflichtige Quelle, API, Datenbank, KI oder Cloud als Voraussetzung | Quellenregister `sources/registry.py`: kostenpflichtige Anbieter haben `legal_status = excluded_paid`, `implemented = False`; das Gateway verweigert jeden Abruf (Test `test_blocked_source_makes_no_network_call`) |
| Nichts Kostenpflichtiges wird automatisch aktiviert | Es gibt keinen Codepfad, der einen Bezahl-Schlüssel liest. Einziger optionaler Schlüssel ist der **kostenlose** Companies-House-Schlüssel (Umgebungsvariable); ohne ihn ist die Quelle aus |
| Keine Abhängigkeit von Claude oder anderen KI-Abos | Standard ist `NullProvider`; optionale lokale KI nur über Loopback (`llm/providers.py` lehnt Cloud-Hosts ab) |
| Nur freie Open-Source-Software | Laufzeit: `httpx` (BSD-3), `SQLAlchemy` (MIT), `openpyxl` (MIT) + Python-Standardbibliothek; vollständige Liste mit geprüften Lizenzen: `docs/DEPENDENCIES_AND_COSTS.md` |
| Betrieb auf eigenem Windows-PC oder eigenem Server | SQLite-Datei lokal; PostgreSQL optional selbst gehostet (`pip install .[postgres]`, psycopg LGPL-3.0) |

**Was „kostenlos“ nicht bedeutet:** unbegrenzte Daten. Freie Quellen haben Abruflimits und
Lücken (insbesondere Umsatz und Mitarbeiter deutscher KMU). Die Anwendung macht diese Lücken
sichtbar, statt sie zu füllen. Strom, Internetanschluss, vorhandene Hardware und ggf. eine
eigene Domain verursachen weiterhin Kosten außerhalb der Software.

## 3. Architekturentscheidungen (ADRs, Revision 2)

| ADR | Entscheidung | Stand im Prototyp |
|---|---|---|
| ADR-01 | Python ≥ 3.12, `ruff`, `mypy --strict`, `pytest` | umgesetzt; `mypy --strict` und `ruff` ohne Befund |
| ADR-02 | Modularer Monolith mit klarer Schichtung: `domain` ← `repo`/Dienste ← `sources`/`http`/`llm` ← `cli` | umgesetzt; Schichtprüfung per `import-linter` folgt in M0b |
| ADR-03 | Oberfläche: Prototyp = Kommandozeile `mi` + HTML-Bericht + XLSX/CSV/JSON. Web-UI (FastAPI, Jinja2, HTMX – alle frei, MIT/BSD) ab M6, lokal an `127.0.0.1` | CLI und Berichte umgesetzt |
| ADR-04 | SQLAlchemy 2.0 Core; **SQLite** für Einzelplatz (Standard), **PostgreSQL** selbst gehostet für Team; Beträge exakt (Dezimal-Text bzw. `NUMERIC`) | SQLite umgesetzt und getestet; PostgreSQL-DDL (inkl. Trigger) vorhanden, **noch nicht gegen echte PostgreSQL-Instanz getestet** |
| ADR-05 | Runs aus persistenten Aufgaben, pausierbar (Strg+C) und fortsetzbar (`mi resume`), ohne Redis/Celery | umgesetzt (sequenziell, ein Prozess); Lease/Mehr-Worker ab M3b |
| ADR-06 | Ein zentrales `FetchGateway` für jeden Internetzugriff | umgesetzt, siehe §4.2 |
| ADR-07 | Beobachtungen append-only; Korrekturen als neue Zeilen; gültiger Wert ist abgeleitet | umgesetzt inkl. SQLite-Triggern (Test `test_observations_are_append_only`) |
| ADR-08 | Extraktion regelbasiert; **optionale lokale KI** (Ollama) nur mit geprüftem wörtlichem Zitat; ohne KI voll funktionsfähig | umgesetzt (`extraction/*`, `llm/providers.py`, `llm/assistant.py`) |
| ADR-09 | Entity Resolution: automatische Zuordnung nur über starke Identifikatoren (LEI, Wikidata-QID, SEC-CIK …); Namensähnlichkeit → Kandidat zur Prüfung | umgesetzt (`resolution.py`, `repo.resolve_or_create`, `engine/adapters.resolve_named`, `engine/matching.py`) |
| ADR-10 | Scoring transparent, versioniert, renormiert bei fehlenden Indikatoren | umgesetzt (`scoring.py`, Modell `v1-entwurf`) |
| ADR-11 | Benutzer/Rollen erst mit Teambetrieb (OIDC über eigenen/freien Identity-Provider, z. B. vorhandenes Entra ID oder selbst gehostetes Keycloak – Apache-2.0) | geplant (M15) |
| ADR-12 | Keine Geheimnisse in Dateien; nur Umgebungsvariablen bzw. Windows-Anmeldespeicher | umgesetzt für Konfiguration |
| ADR-13 *(neu)* | **Keine kostenpflichtige Such-API.** Internetrecherche = Kombination freier, rechtlich nutzbarer Quellen (Register, Wikidata, Ausschreibungen, Nachrichtenindex, Unternehmenswebseiten). Allgemeine Websuche à la Google ist bewusst **nicht** Teil des Kerns | umgesetzt, siehe §5 und `RESEARCH_SOURCES.md` §3 |

## 4. Komponenten (implementiert, Version 0.4.0)

```
src/market_intel/
  domain.py, config.py, db.py, repo.py, resolution.py   Kern (Revision 2), erweitert um Phase-2-Tabellen
  http/gateway.py        einziger Netzzugang: Freigabe, SSRF, robots, TDM, Cache, Rate Limit
                         (konfigurierbar je Quelle), Wiederholungen (konfigurierbar), Timeouts,
                         Abruf- und Kostenbudget, Protokoll jedes Abrufs
  sources/               Abruf + Parser je Anbieter: gleif, wikidata, sec_edgar, companies_house,
                         ecb, ted, gdelt, website, eurostat, user_data; registry (Rechtsstatus,
                         Kosten); common (robuste JSON-Auswertung)
  extraction/            text (HTML, Zitatprüfung), numbers (Geld, Skala, Währung, Zeitraum,
                         Mitarbeiter), documents (PDF, Impressum-IDs, JSON-LD-Stellen,
                         Pressemeldungen, Berichtslinks)
  llm/                   providers (Null, Ollama, OpenAI-kompatibel lokal), assistant (KI-Aufgaben
                         mit deterministischer Prüfung)
  engine/                strategy (Schritt 1), adapters (einheitliche Adapter-Schnittstelle),
                         extract (Schritt 4), matching (5), validation (6), changes (7),
                         derive (EUR, Lücken, Fit), report (8), budget
  research.py            Auftragsausführung: 8 Schritte, dynamische Aufgaben, Budget,
                         Pause/Fortsetzen, Fortschritt, Fehlerkennzeichnung
  analytics.py, scoring.py, export.py, proof.py, cli.py
  intel/                 Phase 3 – Company Intelligence (ohne Netz, nur Datenbank):
                         timeseries (reine Berechnung: Δ, CAGR, Brüche, Lücken, Übernahmen),
                         series (Umsatz-/Mitarbeiterreihen, Segmente, Personal-Dynamik),
                         footprint (Standorte/Märkte in drei Stufen, Kartendaten),
                         competitors (Einordnung ohne Marktanteile), profile (alle Abschnitte
                         mit Quellenverzeichnis), manual (Erfassung mit Beleg)
  extraction/profile.py  schema.org-Organisation, Länder in Standort-/Vertriebssätzen,
                         Reichweite, Vertriebswege, Zielgruppen, Produktüberschriften
  engine/changes.py      + Profiländerungen (Standorte, Märkte, Produkte, Ereignisse, Berichte)
  ui/                    lokale Weboberfläche (Standardbibliothek http.server, kein Framework):
                         render (HTML, alles escaped), server (127.0.0.1, CSRF, CSP, Host-Prüfung),
                         static/app.js (Diagramme, Karte, Abschnitte – ohne Bibliotheken), app.css
  cli_intel.py           Kommandos profile, ui, competitors, history, note, add-*
  products.py            Phase 4 – Produktmatrix laden, prüfen, CSV/TOML-Pflege, in DB ablegen
  opportunity/           Phase 4 – Customer Discovery (ohne Netz, nur Datenbank + Matrix):
                         evidence (Belege in Unternehmensdokumenten), growth (Signale mit
                         Belegstufe), assess (Hypothese + drei Werte + Segment), model (Gewichte,
                         Segmente, Prüfregeln), run (alle Anwendungsfälle je Firma), report (elf
                         Abschnitte), watch (Watchlist mit Historie), leads (CSV/XLSX/JSON)
  cli_opportunity.py     Kommandos products, opportunity-model, opportunities, opportunity,
                         watch, leads
  data/                  world_map.json (Natural Earth, gemeinfrei), country_names.json (MIT),
                         profile_lexicon.toml, THIRD_PARTY_NOTICES.md
```

**Entscheidung Bewertung (ADR-13, Phase 4):** Scoring v1 (ein Gesamtwert, Fit 35 %) wurde
ersetzt. Grund: Ein einziger Wert vermischte Belegqualität, Größe und Datenlage; ein großes
Unternehmen ohne Fit konnte über Größe/Wachstum Punkte sammeln. v2 trennt Produktrelevanz,
Potenzial und Datenqualität, setzt die Priorität ohne Fit-Beleg auf 0 und erzwingt beim Laden,
dass Produkt-Fit stärker gewichtet ist als Größe. Hypothesen werden deterministisch aus Belegen
zusammengesetzt; die optionale lokale KI hat keinen Einfluss auf Hypothese oder Bewertung.

**Entscheidung Oberfläche (ADR-12, Phase 3):** Statt FastAPI/Jinja2/ECharts (in Revision 2
geplant) nutzt die Oberfläche nur die Python-Standardbibliothek und eigenes JavaScript ohne
Bibliotheken. Gründe: keine neue Abhängigkeit (Lieferkette, Lizenzprüfung), läuft offline,
identisches Rendering für Server und eigenständigen HTML-Export, strenge CSP ohne Inline-Skripte
möglich. Grenze: einfacher Ein-Benutzer-Betrieb; für den Teamserver (Anmeldung, Rollen) bleibt
M15 offen. Karten-Kacheln (z. B. OpenStreetMap) werden **nicht** geladen: Die Kachel-Nutzung
ist an Bedingungen gebunden und wäre ein externer Dienst; stattdessen eingebettete
Ländergrenzen (Natural Earth, gemeinfrei).

Der frühere feste Ablauf (`profile.py`, `discovery.py`) ist vollständig in die Adapter und
die Pipeline überführt worden; es gibt kein paralleles System.

### 4.1 Kommandos

| Befehl | Wirkung |
|---|---|
| `mi research "Freitext" [--country … --industry … --company … --use-case … --metric … --years N --list … --refresh] [--max-requests … --max-candidates … --max-seconds … --max-llm-calls … --max-hits …]` | Rechercheauftrag in 8 Schritten |
| `mi status <id>` | Fortschritt je Schritt (ok / Fehler / übersprungen / offen) und Fehlerliste |
| `mi resume <id> [--max-requests …]` | pausierten oder budgetbegrenzten Auftrag fortsetzen |
| `mi report <id>` | Bericht neu erzeugen |
| `mi import-companies datei.csv --list-name …` | Messe-/Verbands-/CRM-Liste importieren |
| `mi import-document datei.pdf --company …` | bereitgestelltes Dokument (z. B. Jahresabschluss) auswerten |
| `mi prove` | Funktionsnachweis (Kriterien 1–9, ohne KI) |
| `mi sources`, `mi adapters`, `mi llm-status` | Quellen, Adapter, lokale KI |
| `mi ui [--port 8765]` | lokale Oberfläche (nur 127.0.0.1) mit Profil, Diagrammen, Karte, Notizen, Wettbewerb |
| `mi profile --company … [--years 5–10] [--estimates] [--html f.html] [--json f.json]` | Profil als Text, eigenständige HTML-Datei oder JSON |
| `mi competitors --company … [--with …] [--store]` | Wettbewerbsanalyse ohne Marktanteile |
| `mi history --company …` | erkannte Veränderungen mit Ereignis- und Erfassungsdatum |
| `mi note --company … --author … "Text"` | interne Notiz |
| `mi add-figure / add-event / add-location / add-fact / add-market … --file beleg.pdf --quote "…"` | Erfassung mit Beleg; Zitat muss wörtlich im Dokument stehen |
| `mi research "…" --use-case all` | branchenübergreifende Kandidatensuche über alle 21 Anwendungsfälle |
| `mi products list/show/validate/export-csv/import-csv` | Produktmatrix ansehen und pflegen |
| `mi opportunities [--company …]` / `mi opportunity --company … [--md f.md]` | bewerten / Ergebnis je Firma (elf Abschnitte) |
| `mi watch --company … --status vorgemerkt --reason … --author … [--comment …]` | Watchlist, Status, Kommentar |
| `mi leads [--out ordner] [--segment …] [--min-priority N]` | Lead-Liste anzeigen oder als CSV/XLSX/JSON exportieren |

### 4.2 Adapter-Schnittstelle

```python
class BaseAdapter:
    key: str; source_keys: tuple[str, ...]; kinds: frozenset[Kind]   # 10 Quellentypen
    finds: bool; investigates: bool
    def enabled(self, ctx) -> bool
    def find_candidates(self, ctx, strategy, cases) -> list[Candidate]     # Schritt 2
    def investigate(self, ctx, company_id, strategy, cases) -> Outcome    # Schritt 3
```

Registriert: GLEIF, Wikidata, Companies House, Firmenlisten, TED, GDELT, Eurostat, SEC EDGAR,
Unternehmenswebseiten. Welche Daten jeder Anbieter tatsächlich liefert und wie er getestet
ist: `docs/PROVIDERS.md`.

## 5. Ablauf eines Rechercheauftrags

| Schritt | Aufgaben | Inhalt |
|---|---|---|
| 1 Suchstrategie | `plan` | Freitext + Filter → Absichten, Länder, genannte Firmen, Namens-/Branchen-/Webbegriffe (Fachlexikon `data/lexicon.toml`), Anwendungsfälle, Unternehmensarten, Kennzahlen, Zeitraum, NACE-Codes; Herkunft jedes Begriffs (`filter`/`rule`/`use_case`/`llm`) |
| 2 Kandidaten finden | `resolve_named`, `find` je Adapter, `select` | genannte Firmen auflösen; Kandidaten je Adapter mit Begründung/Beleg (`run_candidate`); Auswahl: genannte zuerst, dann Anzahl unabhängiger Quellen, Fit, Website; Obergrenze `max_candidates` |
| 3 Quellen untersuchen | `investigate` je Firma × Adapter | Register, SEC, Website (inkl. IR-PDFs, Karriere, Presse), Nachrichten; kürzlich untersuchte Quellen werden übersprungen (`refresh_days`, `--refresh`) |
| 4 Extrahieren | `extract` je Firma | Umsatz, Mitarbeiter, Kennungen, Stellen, Signale, Fit-Belege; optional KI-Klassifikation mit Zitatprüfung |
| 5 Abgleichen | `match` | Prüfkandidaten für Dubletten/Tochtergesellschaften (`match_candidate`); Zusammenführung nie automatisch ohne starke Kennung |
| 6 Validieren | `validate` | Plausibilität beim Speichern (Währung, Spannen, Zeitraum, ganze Mitarbeiterzahl), Widersprüche, Umsatz je Mitarbeiter, Datenlücken; optional KI-Widerspruchshinweise (ungeprüft gekennzeichnet) |
| 7 Speichern | `store` | EUR-Ableitung, Bewertung, Änderungsereignisse (`change_event`), Export |
| 8 Bericht | `report` | `bericht.md/.html/.json`: Strategie, Ablauf, Ergebnis/Rangfolge, Firmen mit [Qn]-Belegen und [Berechnung]-Kennzeichnung, Unsicherheiten, offene Fragen, Quellenverzeichnis mit Abrufzeit, Veröffentlichungsdatum nur falls bekannt, Hinweis bei später unerreichbaren Quellen |

Eine Aufgabe gilt als **fehlgeschlagen**, wenn sie eine Ausnahme wirft oder alle ihre
Quellenabrufe scheitern; als **übersprungen**, wenn sie nicht anwendbar ist oder die Quelle
kürzlich untersucht wurde. Fehler beenden nie den Auftrag. Budgetgrenzen (Abrufe, Aufgaben,
Zeit, KI-Aufrufe, Kosten 0 €) beenden ihn mit `budget_exhausted`; `mi resume` setzt mit
höherem Budget fort.

## 6. Fehlerbehandlung und Nachvollziehbarkeit

- Fällt eine Quelle aus, scheitert nur der Abruf; der Run läuft weiter und endet mit
  `completed_with_gaps`. Hinweise stehen im Run-Bericht, jeder Abruf im `fetch_log`.
- Eine Aufgabe, die unerwartet scheitert (z. B. Formatänderung einer API), wird mit Fehler
  gespeichert; die übrigen Aufgaben laufen weiter.
- Gemessenes Verhalten ohne Internetzugang: siehe `IMPLEMENTATION_ROADMAP.md` §0.

## 7. Betriebsarten (ohne Pflicht-Cloud)

| Modus | Datenbank | Ablauf | Kosten der Software |
|---|---|---|---|
| Einzelplatz Windows/Linux | SQLite-Datei | `mi research` / `mi prove`, Aufgabenplanung über Windows-Aufgabenplanung oder cron | 0 € |
| Teamserver (eigene Hardware/VM) | PostgreSQL selbst gehostet | Web-UI + Worker (ab M6/M15) im LAN, Zugriff per VPN statt öffentlicher Erreichbarkeit | 0 € |
| Öffentliche Erreichbarkeit | – | nicht erforderlich; falls gewünscht über vorhandene Infrastruktur, nie Voraussetzung | – |

## 8. Risiken

| # | Risiko | Gegenmaßnahme |
|---|---|---|
| R1 | Freie Quellen decken Umsatz/Mitarbeiter deutscher KMU nur lückenhaft ab | Lücken sichtbar; Webseiten-Selbstangaben; Nutzer-Upload von Jahresabschluss-PDFs (M8) |
| R2 | Keine freie, kommerziell nutzbare allgemeine Websuche | Discovery über strukturierte Quellen; Webseiten nur von bekannten Domains; Dokumentation der Grenze |
| R3 | API-Formatänderungen (TED-Feldnamen, Wikidata, GDELT) | Vertragstests, tolerante Parser, Aufgabe scheitert isoliert; Live-Smoke-Test `mi prove` |
| R4 | Rechtsstatus einzelner Quellen ändert sich | Register mit Rechtsstatus; Abruf nur bei Freigabe |
| R5 | Fehlzuordnung von Firmen | nur starke IDs automatisch; Kandidaten im Run-Bericht |
| R6 | Lokale KI langsam/fehlerhaft | optional; Zitatprüfung; Regeln bleiben die Grundlage |
| R7 | PostgreSQL-Pfad ungetestet | CI-Matrix mit PostgreSQL-Service (M0b) |
| R8 | Proxy-/Firmennetz blockiert Quellen | `mi prove` zeigt je Quelle, was blockiert ist; Freigabe der Hosts in der Firewall |
