# Verbindliche Übersicht: Abhängigkeiten, Datenquellen und Kosten

Stand: 2026-10-18 · gilt für market-intel 0.1.0

**Ergebnis:** Die Kernanwendung benötigt **keine** kostenpflichtige Software, keinen
kostenpflichtigen Dienst, kein Abonnement, keine Cloud und keinen KI-Zugang. Optionale Kosten
entstehen nur, wenn jemand bewusst einen Bezahldienst ergänzt – dafür gibt es im Code keinen
aktiven Weg.

Nicht Teil der Software, aber real: Strom, Internetanschluss, vorhandene Hardware, ggf. eigene
Domain/Zertifikat für einen Teamserver.

## 1. Python-Pakete (Lizenzen am 2026-10-18 mit `pip-licenses` bzw. Paket-Metadaten geprüft)

| Paket | Version | Lizenz | Zweck | Pflicht | Kosten | Externer Dienst |
|---|---|---|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | HTTP-Client | ja | 0 € | nein |
| httpcore | 1.0.9 | BSD-3-Clause | von httpx | ja | 0 € | nein |
| h11 | 0.16.0 | MIT | von httpx | ja | 0 € | nein |
| anyio | 4.15.1 | MIT | von httpx | ja | 0 € | nein |
| idna | 3.20 | BSD-3-Clause | von httpx | ja | 0 € | nein |
| certifi | 2026.7.22 | MPL-2.0 | CA-Zertifikate | ja | 0 € | nein |
| SQLAlchemy | 2.0.43 | MIT | Datenbankzugriff | ja | 0 € | nein |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | von SQLAlchemy | ja | 0 € | nein |
| typing_extensions | 4.16.0 | PSF-2.0 | von SQLAlchemy | ja | 0 € | nein |
| openpyxl | 3.1.5 | MIT | XLSX-Export | ja | 0 € | nein |
| et_xmlfile | 2.0.0 | MIT | von openpyxl | ja | 0 € | nein |
| psycopg[binary] | 3.2.10 | LGPL-3.0 (bündelt libpq, PostgreSQL License) | nur für PostgreSQL | nein (`.[postgres]`) | 0 € | nein |
| pytest, mypy, ruff, pip-licenses | 8.4.2 / 1.18.1 / 0.13.0 / 5.0.0 | MIT | Entwicklung | nein (`.[dev]`) | 0 € | nein |
| types-openpyxl | 3.1.5.20250919 | Apache-2.0 | Entwicklung | nein | 0 € | nein |

Python selbst: PSF-2.0. SQLite: gemeinfrei (in Python enthalten). Keine Komponente verlangt
Lizenzgebühren. psycopg (LGPL) wird unverändert als Bibliothek genutzt.

Geplante Komponenten für Web-UI/Teambetrieb (noch nicht installiert, Lizenz bei Aufnahme
erneut zu prüfen): FastAPI (MIT), Uvicorn (BSD-3), Jinja2 (BSD-3), HTMX (BSD-2/0BSD),
Apache ECharts (Apache-2.0), PostgreSQL (PostgreSQL License), Keycloak (Apache-2.0, optional).

## 2. Datenquellen

| Name | Lizenz | Kostenloser Umfang | Einschränkungen | Registrierung | Zugangsdaten | Optionale Kosten | Externer Dienst | Standard |
|---|---|---|---|---|---|---|---|---|
| GLEIF LEI API | CC0 1.0 (laut Wiederverwendern) | vollständig | nur Firmen mit LEI; L2 ohne Prozente | nein | nein | keine | ja (api.gleif.org) | aktiv |
| Wikidata | CC0 1.0 | vollständig | SPARQL 60 s/min; lückenhaft | nein | nein (User-Agent mit Kontakt) | keine | ja | aktiv |
| SEC EDGAR | gemeinfrei | vollständig | 10 Anfragen/s; nur SEC-Melder | nein | nein (User-Agent mit E-Mail) | keine | ja | aktiv |
| EZB Data Portal | Wiederverwendung mit Quellenangabe | vollständig | nur Kurse | nein | nein | keine | ja | aktiv |
| TED Search API | Wiederverwendung mit Quellenangabe | vollständig | 100 Treffer/Seite | nein | nein | keine | ja | aktiv |
| GDELT DOC 2.0 | frei mit Zitat/Link | gedrosselt | nur Metadaten speichern | nein | nein | keine (GDELT Cloud wäre separat kostenpflichtig – nicht genutzt) | ja | aktiv |
| Unternehmenswebseiten | Urheberrecht; TDM nach § 44b UrhG | 8 Seiten/Domain | robots.txt, TDM-Vorbehalt | nein | nein | keine | ja | aktiv |
| Companies House | OGL | 600 Anfragen/5 min | nur UK | ja, kostenlos | kostenloser Schlüssel | keine | ja | aus |
| Destatis GENESIS | DL-DE BY 2.0 | vollständig | Token über kostenloses Konto (S) | ja, kostenlos | Token | keine | ja | geplant |
| Eurostat | Wiederverwendung mit Quellenangabe | vollständig | NACE-Bruch 2025 | nein | nein | keine | ja | geplant |
| Handelsregister | amtlich | Einzelabrufe | 60/h, kein Massenabruf | – | – | Dokumente teils gebührenpflichtig | – | gesperrt |
| BA Jobsuche | ungeklärt | – | keine offizielle API | – | – | – | – | gesperrt |
| Marginalia | CC BY-NC-SA 4.0 (kostenloser Schlüssel) | nur nicht-kommerziell | kommerziell kostenpflichtig | E-Mail | Schlüssel | ja | – | ausgeschlossen |
| North Data | Vertrag | keiner | – | Vertrag | Schlüssel | ab 500 €/Monat | – | ausgeschlossen |
| Brave/Google/Bing-Such-APIs | – | – | kostenpflichtig bzw. eingestellt | – | – | ja | – | ausgeschlossen |

Wenn eine aktive Quelle ausfällt oder ihr Limit erreicht ist, scheitert nur der jeweilige
Abruf; der Lauf endet mit `completed_with_gaps`, und die Lücke steht im Bericht.

## 3. Optionale lokale KI

| Komponente | Lizenz | Kosten | Hinweis |
|---|---|---|---|
| Ollama (Server, lokal) | MIT (laut GitHub-Repository; vor Installation prüfen) | 0 € | nur Loopback (`127.0.0.1:11434`); andere Hosts nur mit `allow_non_loopback = true` (eigener LAN-Server) |
| Modell `qwen3:8b` (Voreinstellung) | Apache-2.0 (S) | 0 € | Download ca. 5 GB; laut Drittquellen ca. 8 GB GPU-Speicher oder ca. 10 GB RAM bei reinem CPU-Betrieb |
| Modell `qwen3:4b` | Apache-2.0 (S) | 0 € | kleiner, für Rechner mit wenig Speicher; geringere Qualität |
| Mistral 7B | Apache-2.0 | 0 € | Alternative |
| Llama 3.x | Llama Community License (nicht OSI; Nutzungsrichtlinie, Schwellen > 700 Mio. Nutzer) | 0 € | nur nach Lizenzprüfung |
| Gemma | Gemma Terms of Use (eigene Bedingungen) | 0 € | nur nach Lizenzprüfung |

Ehrliche Einordnung: Die Geschwindigkeit hängt stark von der Hardware ab. Auf einem Büro-PC
ohne Grafikkarte kann die Auswertung einer Webseite mehrere Sekunden bis Minuten dauern; dies
wurde in dieser Phase **nicht gemessen**. Die KI ist ausschließlich eine Ergänzung: Jede
Funktion (Recherche, Extraktion, Wachstum, Vergleich, Lücken, Zuordnung, Bewertung, Export)
läuft regelbasiert ohne sie.

## 4. Infrastruktur

| Betrieb | Benötigt | Kosten der Software |
|---|---|---|
| Einzelplatz | Windows 10/11 oder Linux, Python ≥ 3.12, Internetzugang zu den Quellen (Firewall-Freigabe der Hosts) | 0 € |
| Teamserver | eigener Rechner/VM, PostgreSQL ≥ 16 (selbst gehostet), Zugriff im LAN/VPN | 0 € |
| Öffentlich erreichbar | nicht erforderlich | – |
