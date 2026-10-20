# Yellotools Market Intelligence

Recherche- und Analysewerkzeug für Marktführer-Analyse, Customer Discovery und die
mehrjährige Beobachtung von Unternehmen. **100 % kostenloser Betrieb:** keine Bezahl-APIs,
keine Abos, keine Cloud, keine KI-Dienste. Jede Zahl ist auf eine Quelle zurückführbar;
fehlende Daten bleiben sichtbar unbekannt.

Stand: Version 0.3.0 – Research Engine und Unternehmensanalyse (Profile, Entwicklung über 5–10 Jahre, Standorte, Wettbewerb, Änderungen) mit lokaler Oberfläche.

## Schnellstart (Windows)

Voraussetzung: Python 3.12 oder neuer (kostenlos, python.org) und Internetzugang zu den Quellen.

```bat
cd MARKT-ANALYSE
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
copy market_intel.example.toml market_intel.toml
notepad market_intel.toml          &rem contact_email eintragen
mi sources                         &rem Quellen, Lizenzen, Grenzen, Kosten (= keine)
mi prove                           &rem realer Funktionsnachweis, Bericht in proof\PROOF_REPORT.md
```

Linux/macOS: `python3 -m venv .venv && . .venv/bin/activate && pip install -e .`, Rest gleich.

## Befehle

| Befehl | Zweck |
|---|---|
| `mi research "Finde die größten Hersteller von Beschriftungs- und Montagewerkzeugen in Deutschland."` | Rechercheauftrag in eigenen Worten (8 Schritte, Bericht in `out/bericht.md`) |
| `mi research "Analysiere die Entwicklung von „ORAFOL“ über die letzten fünf Jahre"` | Firmen in „…“ werden direkt aufgelöst |
| `mi research --company 3M "Avery Dennison" --metric revenue headcount_total --years 5` | nur Filter, ohne Freitext |
| `mi research "…" --use-case glasfolien lackschutz_aufbereitung --country DE AT --max-candidates 30` | Kundensuche je Anwendungsfall |
| `mi status <id>` / `mi resume <id>` | Fortschritt je Schritt / Fortsetzen nach Strg+C oder Budgetende |
| `mi import-companies aussteller.csv --list-name "Messe 2026"` und `mi research … --list "Messe 2026"` | Messe-/Verbandslisten einbeziehen |
| `mi import-document gb2023.pdf --company "ORAFOL"` | Jahresabschluss/Geschäftsbericht aus dem Unternehmensregister auswerten |
| `mi prove` | Funktionsnachweis (Kriterien 1–9) |
| `mi sources` / `mi adapters` / `mi llm-status` | Quellen, Adapter, lokale KI |
| `mi ui` → http://127.0.0.1:8765/ | Oberfläche: Überblick, Finanzen, Mitarbeiter, Standorte, Produkte, Märkte, Historie, Quellen, Notizen |
| `mi profile --company "ORAFOL" --years 10 --html orafol.html` | Unternehmensprofil als Datei (ohne Server) oder Text |
| `mi competitors --company "ORAFOL" --store` | Wettbewerbsanalyse (direkt/indirekt/verwandt, keine Marktanteile) |
| `mi history --company "ORAFOL"` | Veränderungen zwischen Recherchen (Ereignis- und Erfassungsdatum) |
| `mi add-event --company "ORAFOL" --type acquisition --date 2022-07 --file gb2022.pdf --quote "…"` | belegte Übernahme erfassen (macht Wachstum „nicht organisch“) |
| `mi add-figure --company … --metric headcount_total --value 350 --period-end 2023-12-31 --segment country:DE --file … --quote "…"` | Kennzahl mit Beleg (auch je Land/Standort, Schätzung nur mit `--methodology`) |

Ergebnisse: `bericht.md` und `bericht_auftrag.html` (Fakten mit Quellenverweis [Qn],
Berechnungen gekennzeichnet, Unsicherheiten, offene Fragen, Quellenverzeichnis) und im Ordner
`export/` CSV/XLSX/JSON (jede Zahl mit Quelle, Zeitraum, Herkunftsklasse, Prüfstatus, Zitat).
Welche Anbieter mit welchen Funktionen angebunden sind: [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Wichtig zu wissen

- **Kostenlos heißt nicht unbegrenzt.** Freie Quellen haben Limits und Lücken; Umsatz und
  Mitarbeiter deutscher KMU sind oft nicht frei verfügbar. Das Programm zeigt das als Lücke.
- **Keine allgemeine Websuche.** Es gibt keine kostenlose, kommerziell zulässige Such-API.
  Firmen werden über GLEIF, Wikidata und EU-Ausschreibungen gefunden, belegt über ihre eigenen
  Webseiten (robots.txt und TDM-Vorbehalt werden beachtet). Details: `RESEARCH_SOURCES.md`.
- **Anwendungsfälle sind Hypothesen** (`src/market_intel/data/use_cases.toml`) und müssen vom
  Vertrieb bestätigt werden.
- **Unbekannt ist nicht null.** Fehlende Jahre erscheinen als Lücke, nie als 0 oder Stillstand;
  Wachstum wird nur zwischen vergleichbaren Werten berechnet (Regeln:
  `PRODUCT_SPECIFICATION.md` §7a).
- **Lokale KI ist optional** (Ollama). Hardwarehinweise: `docs/DEPENDENCIES_AND_COSTS.md`.
- **Firmennetz:** Wenn ein Proxy oder eine Firewall die Quellen sperrt, zeigt `mi prove` je
  Quelle den Fehler. Benötigte Hosts: api.gleif.org, www.wikidata.org, query.wikidata.org,
  www.sec.gov, data.sec.gov, data-api.ecb.europa.eu, api.ted.europa.eu,
  api.gdeltproject.org, ec.europa.eu sowie die Webseiten der untersuchten Unternehmen.

## Entwicklung

```bash
pip install -e ".[dev]"
pytest            # 156 Tests mit Mock-Daten (keine echten Abrufe)
pytest tests/manual -m live -s   # manueller Live-Test, siehe docs/MANUAL_INTEGRATION_TEST.md
ruff check src tests && mypy
```

## Dokumente

| Dokument | Inhalt |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Bestandsaufnahme, Entscheidungen (eigenständig, kostenlos), Komponenten, Datenflüsse, Risiken |
| [PRODUCT_SPECIFICATION.md](PRODUCT_SPECIFICATION.md) | Ziele, Anforderungen, Bewertung |
| [DATA_MODEL.md](DATA_MODEL.md) | Datenmodell, Status- und Herkunftsregeln, Entity Resolution |
| [RESEARCH_SOURCES.md](RESEARCH_SOURCES.md) | Quellen mit Lizenz, Limits, Rechtsstatus; Such-API-Ersatz |
| [docs/DEPENDENCIES_AND_COSTS.md](docs/DEPENDENCIES_AND_COSTS.md) | verbindliche Kosten- und Lizenzübersicht |
| [docs/PROVIDERS.md](docs/PROVIDERS.md) | angebundene Anbieter, Funktionen, Teststatus |
| [docs/MANUAL_INTEGRATION_TEST.md](docs/MANUAL_INTEGRATION_TEST.md) | manueller Test mit realen Unternehmen |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | Stand, Nachweis-Ergebnisse, Meilensteine |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | Bedrohungsmodell, DSGVO, Urheberrecht |
