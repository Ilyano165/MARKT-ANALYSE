# Architektur – Yellotools Market Intelligence

Stand: 2026-10-18 · Revision 2 (verbindlich: **100 % kostenloser Betrieb**) · Prototyp 0.1.0

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
| Keine Abhängigkeit von Claude oder anderen KI-Abos | Standard ist `NullLLM`; optionale lokale KI nur über Loopback (`llm/local.py` lehnt Cloud-Hosts ab) |
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
| ADR-08 | Extraktion regelbasiert; **optionale lokale KI** (Ollama) nur mit geprüftem wörtlichem Zitat; ohne KI voll funktionsfähig | umgesetzt (`extraction/text.py`, `llm/local.py`) |
| ADR-09 | Entity Resolution: automatische Zuordnung nur über starke Identifikatoren (LEI, Wikidata-QID, SEC-CIK …); Namensähnlichkeit → Kandidat zur Prüfung | umgesetzt (`resolution.py`, `repo.resolve_or_create`, `profile.py`) |
| ADR-10 | Scoring transparent, versioniert, renormiert bei fehlenden Indikatoren | umgesetzt (`scoring.py`, Modell `v1-entwurf`) |
| ADR-11 | Benutzer/Rollen erst mit Teambetrieb (OIDC über eigenen/freien Identity-Provider, z. B. vorhandenes Entra ID oder selbst gehostetes Keycloak – Apache-2.0) | geplant (M15) |
| ADR-12 | Keine Geheimnisse in Dateien; nur Umgebungsvariablen bzw. Windows-Anmeldespeicher | umgesetzt für Konfiguration |
| ADR-13 *(neu)* | **Keine kostenpflichtige Such-API.** Internetrecherche = Kombination freier, rechtlich nutzbarer Quellen (Register, Wikidata, Ausschreibungen, Nachrichtenindex, Unternehmenswebseiten). Allgemeine Websuche à la Google ist bewusst **nicht** Teil des Kerns | umgesetzt, siehe §5 und `RESEARCH_SOURCES.md` §3 |

## 4. Komponenten (implementiert)

```
src/market_intel/
  domain.py          Herkunftsklassen, Prüfstatus, Zeiträume, Entwürfe (ObservationDraft, EvidenceDraft)
  config.py          TOML-Einstellungen ohne Geheimnisse; LLM standardmäßig aus
  db.py              Schema (SQLAlchemy Core), append-only-Trigger (SQLite + PostgreSQL)
  repo.py            einziger Schreibweg: Firmen, IDs, Aliase, Belege, Beobachtungen, Konflikte, Lücken
  resolution.py      Namensnormalisierung, Rechtsformen, Ähnlichkeit, Domains
  http/gateway.py    FetchGateway (siehe 4.2)
  sources/
    registry.py      Quellenregister mit Lizenz, kostenlosem Umfang, Grenzen, Rechtsstatus
    gleif.py         LEI-Suche, Datensatz, Mutterbeziehungen
    wikidata.py      Suche, EntityData, Kennzahlenreihen (P1128, P2139), SPARQL nach Branche
    sec_edgar.py     XBRL companyfacts (Umsatzreihen), CIK-Kandidaten
    ecb.py           EZB-Jahreskurse für abgeleitete EUR-Werte
    ted.py           EU-Ausschreibungen → öffentliche Auftraggeber + Signale
    gdelt.py         Nachrichtenindex → Marktsignale
    website.py       Unternehmenswebseiten (robots/TDM), Mitarbeiter-Selbstangaben, Anwendungsfall-Belege
  extraction/text.py HTML→Text, Zitatprüfung, Zahlenformate, Regeln (Mitarbeiter, Suchbegriffe)
  llm/local.py       LocalLLM-Schnittstelle, NullLLM (Standard), OllamaLLM (optional, nur lokal)
  usecases.py + data/use_cases.toml   Anwendungsfälle (Hypothesen) mit Suchbegriffen je Quelle
  profile.py         Firmenprofil: GLEIF → Wikidata → GLEIF per LEI → SEC → Webseite → EUR → Lücken
  discovery.py       Customer Discovery mit Fit-Regeln strong/plausible/weak
  analytics.py       Zeitreihen, YoY, CAGR nur zwischen vergleichbaren Werten, Vergleich, Datenqualität
  scoring.py         Chancenbewertung mit Zerlegung
  export.py          CSV, XLSX, JSON, HTML-Bericht (Formel-Injektion entschärft)
  research.py        Runs/Aufgaben, Pause/Fortsetzen
  proof.py           Funktionsnachweis: Kriterien 1–9 aus Datenbank und Dateien
  cli.py             mi sources | run | prove | resume | llm-status
```

### 4.1 Kommandos

| Befehl | Wirkung |
|---|---|
| `mi sources` | zeigt jede Quelle mit Status, kostenlosem Umfang, Registrierung, optionalen Kosten |
| `mi run --compare … --use-case … --country DE` | Recherchelauf (Profil + Discovery + Scoring + Export) |
| `mi prove` | wie `run`, ohne KI, prüft die neun Nachweiskriterien und schreibt `proof/PROOF_REPORT.md` |
| `mi resume <run>` | setzt einen pausierten Lauf fort |
| `mi llm-status` | prüft die optionale lokale KI |

### 4.2 FetchGateway (implementiert, getestet)

Reihenfolge je Abruf: Quellenfreigabe (Register + aktiviert) → SSRF-Schutz (nur `https`, Port
443, keine privaten/Loopback/Link-Local-Adressen nach DNS-Auflösung, keine internen Namen,
keine Zugangsdaten in URLs) → robots.txt (bei Webseiten) → Cache (frisch: kein Netz; sonst
`If-None-Match`/`If-Modified-Since`) → Anfragelimit je Run → Drosselung je Quelle/Host →
Abruf mit Größenlimit → Wiederholung bei 408/425/429/5xx und Netzwerkfehlern (max. 3 Versuche,
`Retry-After` max. 60 s) → TDM-Vorbehalt (`tdm-reservation` Header/Meta) → Speicherung als
`source_document` (SHA-256) → Protokoll in `fetch_log`. **Jeder** Aufruf, auch ein gesperrter
oder gescheiterter, wird protokolliert. Weiterleitungen werden nicht blind verfolgt; der
Webseiten-Adapter folgt nur innerhalb derselben Domain und lässt jedes Ziel neu prüfen.

### 4.3 Optionale lokale KI

`LocalLLM`-Protokoll mit `available()` und `classify_use_cases()`. Implementierungen:
`NullLLM` (Standard) und `OllamaLLM` (`/api/tags`, `/api/chat` mit JSON-Schema im Feld
`format`, Temperatur 0). Ergebnisse ohne wörtliches, im Seitentext gefundenes Zitat werden
verworfen (Test `test_ollama_results_need_verified_quotes`). Ist Ollama nicht erreichbar,
läuft alles regelbasiert weiter. Modell- und Hardwarehinweise: `docs/DEPENDENCIES_AND_COSTS.md` §3.

## 5. Datenflüsse

### 5.1 Firmenprofil (`profile.py`)

1. GLEIF-Volltextsuche; Übernahme nur bei Namensähnlichkeit ≥ 0,85, sonst Kandidat.
2. Wikidata-Suche; Zusammenführung nur bei gleichem LEI; neue Firma nur, wenn noch keine
   existiert und Label-Ähnlichkeit ≥ 0,95 und der Eintrag Firmenmerkmale hat.
3. Liefert Wikidata einen LEI, wird der GLEIF-Datensatz direkt per LEI geladen.
4. SEC-Umsatzreihe nur bei CIK aus starkem Identifikator (Wikidata P5531); Namenstreffer → Kandidat.
5. Webseite (aus Wikidata P856) → Mitarbeiter-Selbstangaben, Anwendungsfall-Belege.
6. Nicht-EUR-Umsätze → abgeleitete EUR-Werte mit EZB-Jahresdurchschnitt (Methodik gespeichert).
7. Fehlende Kernkennzahlen → `data_gap` mit geprüften Quellen.

### 5.2 Customer Discovery (`discovery.py`)

Anwendungsfall × Land → (a) GLEIF-Namenssuche mit `name_terms` → Fit `plausible`;
(b) Wikidata-Branchen aus `industry_terms` → SPARQL Firmen des Landes → Fit `weak`;
(c) Webseiten dieser Firmen → Fit `strong` (≥ 2 Begriffe) bzw. `plausible` (1 Begriff);
(d) TED-Ausschreibungen → öffentliche Auftraggeber + Signal, Fit `weak` (der Auftraggeber zeigt
Bedarf; Kunde von Yellotools ist eher der ausführende Betrieb); (e) GDELT → Marktsignale.
Danach Scoring je Anwendungsfall.

### 5.3 Aufnahme einer Beobachtung (`repo.ingest_observation`)

Validierung im `ObservationDraft` (Methodik bei abgeleiteten Werten, Währung bei Beträgen,
Zeitraumlogik) → Duplikat (gleicher Inhalts-Hash) nur `last_seen_at` → Ablehnung ohne
verifiziertes Zitat → Speichern mit Startstatus nach Herkunftsklasse → Konfliktprüfung mit
Toleranz; unbekannter Konsolidierungskreis gilt als vergleichbar mit jedem (sonst blieben
Widersprüche unsichtbar – im Test gefunden und behoben).

## 6. Fehlerbehandlung und Nachvollziehbarkeit

- Fällt eine Quelle aus, scheitert nur der Abruf; der Run läuft weiter und endet mit
  `completed_with_gaps`. Hinweise stehen im Run-Bericht, jeder Abruf im `fetch_log`.
- Eine Aufgabe, die unerwartet scheitert (z. B. Formatänderung einer API), wird mit Fehler
  gespeichert; die übrigen Aufgaben laufen weiter.
- Gemessenes Verhalten ohne Internetzugang: siehe `IMPLEMENTATION_ROADMAP.md` §0.

## 7. Betriebsarten (ohne Pflicht-Cloud)

| Modus | Datenbank | Ablauf | Kosten der Software |
|---|---|---|---|
| Einzelplatz Windows/Linux | SQLite-Datei | `mi run` / `mi prove`, Aufgabenplanung über Windows-Aufgabenplanung oder cron | 0 € |
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
