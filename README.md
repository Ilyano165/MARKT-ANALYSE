# Yellotools Market Intelligence

Recherche- und Analysewerkzeug für Marktführer-Analyse, Customer Discovery und die
mehrjährige Beobachtung von Unternehmen. **100 % kostenloser Betrieb:** keine Bezahl-APIs,
keine Abos, keine Cloud, keine KI-Dienste. Jede Zahl ist auf eine Quelle zurückführbar;
fehlende Daten bleiben sichtbar unbekannt.

Stand: Prototyp 0.1.0 (Kommandozeile + Exporte). Web-Oberfläche folgt (siehe Roadmap).

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
| `mi sources` | alle Quellen mit Status, kostenlosem Umfang, Registrierung, optionalen Kosten |
| `mi run --compare 3M "Avery Dennison" ORAFOL --use-case fahrzeugfolierung glasfolien --country DE` | Recherche: Profile, Vergleich, Kundensuche, Bewertung, Export nach `out/export` |
| `mi prove` | wie `run`, ohne KI; prüft die neun Nachweiskriterien |
| `mi resume <run-id>` | pausierten Lauf fortsetzen (Pause mit Strg+C) |
| `mi llm-status` | optionale lokale KI prüfen |

Ergebnisse: `vergleich.csv`, `chancen.csv`, `kennzahlen.csv` (jede Zahl mit Quelle, Zeitraum,
Herkunftsklasse, Prüfstatus, Zitat), `signale.csv`, `luecken.csv`, `quellenaufrufe.csv`,
`market_intel_export.xlsx`, `market_intel_export.json`, `bericht.html`.

## Wichtig zu wissen

- **Kostenlos heißt nicht unbegrenzt.** Freie Quellen haben Limits und Lücken; Umsatz und
  Mitarbeiter deutscher KMU sind oft nicht frei verfügbar. Das Programm zeigt das als Lücke.
- **Keine allgemeine Websuche.** Es gibt keine kostenlose, kommerziell zulässige Such-API.
  Firmen werden über GLEIF, Wikidata und EU-Ausschreibungen gefunden, belegt über ihre eigenen
  Webseiten (robots.txt und TDM-Vorbehalt werden beachtet). Details: `RESEARCH_SOURCES.md`.
- **Anwendungsfälle sind Hypothesen** (`src/market_intel/data/use_cases.toml`) und müssen vom
  Vertrieb bestätigt werden.
- **Lokale KI ist optional** (Ollama). Hardwarehinweise: `docs/DEPENDENCIES_AND_COSTS.md`.
- **Firmennetz:** Wenn ein Proxy oder eine Firewall die Quellen sperrt, zeigt `mi prove` je
  Quelle den Fehler. Benötigte Hosts: api.gleif.org, www.wikidata.org, query.wikidata.org,
  www.sec.gov, data.sec.gov, data-api.ecb.europa.eu, api.ted.europa.eu,
  api.gdeltproject.org sowie die Webseiten der untersuchten Unternehmen.

## Entwicklung

```bash
pip install -e ".[dev]"
pytest            # 35 Tests, synthetisches Test-Internet (keine echten Abrufe)
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
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | Stand, Nachweis-Ergebnisse, Meilensteine |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | Bedrohungsmodell, DSGVO, Urheberrecht |
