# Umsetzungsplan – Yellotools Market Intelligence

Stand: 2026-10-23 · Revision 6 (Phase 5 Live-Test; verbindlich: **100 % kostenloser Betrieb**)

Grundsätze: kleine, abnehmbare Meilensteine; jeder endet mit grünen Tests. **Gate „Kosten“
gilt für jeden Meilenstein: keine neue Abhängigkeit, Quelle oder Infrastruktur mit
verpflichtenden Kosten** – Prüfung über `docs/DEPENDENCIES_AND_COSTS.md`.

## 0d. Phase 5 – Live-Test (2026-10-23): NICHT bestanden, Quellen gesperrt

Protokoll: `docs/proof/2026-10-23_live-test_phase5.md`.

- GLEIF, Wikidata, yellotools.com und Firmenwebseiten aus der Entwicklungsumgebung **gesperrt**
  (Proxy CONNECT 403). Beide Live-Tests ausgeführt und fehlgeschlagen (0 Firmen aufgelöst);
  **keine echten Daten**, **keine erfundenen Ergebnisse** (0 Datensätze).
- Behoben: Proxy-Sperren wurden dreimal wiederholt → jetzt ein Versuch (6 statt 18 Abrufe,
  5 statt ~25 s).
- Neu: Ende-zu-Ende-Live-Test mit sechs realen Firmen aus fünf Branchen inkl. Negativbeispiel
  (`tests/manual/test_live_integration.py`), sofort ausführbar, sobald das Netz frei ist.
- Produktmatrix unverändert und weiterhin unverifiziert.
- Tests: 181 bestanden, 2 übersprungen (die beiden Live-Tests ohne `MI_LIVE=1`).

## 0c. Stand nach Phase 4 – Customer Discovery (2026-10-22, Version 0.4.0)

Umgesetzt und getestet (**176 automatische Tests grün, 1 übersprungen (Live-Test)**, `ruff`,
`mypy --strict` ohne Befund; Lead- und Opportunity-Seiten im Headless-Browser geprüft):

- Produktmatrix (12 Produktgruppen, Herstellerquellen, Einsatzgrenzen) und Anwendungsmatrix
  (21 Anwendungsfälle, davon 5 aus dem Portfolio abgeleitet) mit Prüfung und CSV/TOML-Pflege –
  `docs/PRODUCT_MATRIX.md`
- Bedarfshypothese je Unternehmen und Anwendungsfall nur aus wörtlichen Belegen (Tätigkeit,
  Arbeitsprozesse, Gegenindizien, Werkzeug-Erwähnungen als Hinweis); keine Aussagen über
  Bedarf, Lieferanten oder Kaufbereitschaft
- Wachstumssignale mit Belegstufe (bestätigt / abgeleitet / unbestätigt)
- Bewertung v2 (Produktrelevanz, Potenzial, Datenqualität getrennt; konfigurierbar; Fit > Größe
  erzwungen; fehlende Daten neutral), sieben erklärbare Segmente
- Ergebnis je Firma in elf Abschnitten (CLI, Markdown, JSON, Oberfläche), Watchlist mit
  Status-Historie und Kommentaren, Lead-Export CSV/XLSX/JSON (ohne Personendaten)
- Tests mit sieben Testfirmen aus fünf Branchen, Negativfall „groß ohne Fit“ (Priorität 0),
  KI-Behauptung ohne Beleg (bleibt wirkungslos), fehlende Daten (nicht schlechter bewertet)

Bei der Sichtprüfung gefunden und behoben: Wurde nur das Sitzland erkannt, ergab das Neugewichten
über diese eine Komponente „Potenzial 100“. Jetzt gehen unbekannte Komponenten neutral ein
(Regressionstest).

**Nicht erledigt / ehrlich offen:**
- **Produktmatrix nicht vom Hersteller bestätigt** (Status `researched`); Yellotools-Seiten waren
  nur über die Websuche einsehbar. Artikelstamm fehlt.
- **Alle 21 Anwendungsfälle sind Hypothesen**; insbesondere die abgeleiteten Branchen
  (Fuhrpark/Remarketing, Facility, Kunststoffplatten, Bodenmarkierung) und Industrie-/Verpackungs-
  anwendungen sind fachlich unbestätigt.
- Belege entstehen über Begriffstreffer. Verneinungen im selben Teilsatz („bieten keine
  Fahrzeugfolierung an“, „führen wir nicht durch“) werden seit 2026-10-23 erkannt und als
  Gegenbeleg gezeigt; bloße Marktnennungen ohne Verneinung und komplexere Formulierungen
  („früher haben wir …“) nicht. Gegenindizien decken nur Hersteller/Händler ab.
- Die Testfirmen sind erfunden; mit echten Quellen (Live-Test, Abnahme A0) ist die Discovery
  weiterhin **nicht** geprüft. Ohne allgemeine Websuche findet das System nur Firmen, die über
  GLEIF, Wikidata, TED, GDELT oder importierte Listen auftauchen.
- Erledigt 2026-10-23: Bestandskunden-Abgleich (FR-B-07: Listen aus `[crm] customer_lists`,
  Zuordnung über LEI oder Website-Domain) und dauerhafter Ausschluss verworfener Firmen (FR-B-08:
  eigenes Segment, nicht im Lead-Export, in späteren Recherchen nicht erneut ausgewählt –
  namentlich genannte Firmen schon). Offen: Abgleich über Namen ohne Website/LEI (bewusst
  nicht automatisch, Verwechslungsgefahr).

## 0b. Stand nach Phase 3 – Company Intelligence (2026-10-20, Version 0.3.0)

Umgesetzt und getestet (**156 automatische Tests grün, 1 übersprungen (Live-Test)**, `ruff` und
`mypy --strict` ohne Befund; Oberfläche zusätzlich im Headless-Browser geprüft: keine
Skriptfehler, kein horizontales Überlaufen bei 390 px):

- Persistente Profile: offizieller Name, Namen/Marken, Website, Hauptsitz und weitere
  Standorte, Mutter/Töchter, Branche/Teilbranchen, Produktgruppen/Leistungen, Absatzländer,
  Vertriebswege, Zielgruppen, Mitarbeiter, Umsatz, Wachstumshinweise, Quellen mit Abrufzeit
- Umsatzentwicklung 5–10 Geschäftsjahre (Δ absolut/%, Gesamtveränderung, CAGR mit Formel),
  Brüche bei Währung, Geschäftsjahr (Rumpfjahr), Konsolidierungskreis; Übernahmen „nicht
  organisch“; Schätzungen nur auf Wunsch (Regeln: `PRODUCT_SPECIFICATION.md` §7a)
- Mitarbeiterentwicklung mit Personal-Dynamik (Netto ≠ Einstellungen; Abbau-/Einstellungs-
  meldungen und Stellenanzeigen getrennt), Segmente nach Land/Standort
- Standorte/Märkte in drei Stufen, Karte nur mit ausreichend genauen Koordinaten
- Wettbewerbsanalyse direkt/indirekt/verwandt, ohne Marktanteile, mit Historie
- Änderungserkennung bei erneuter Recherche (Ereignisdatum ≠ Erfassungsdatum), Historie bleibt
- Oberfläche `mi ui` mit neun Abschnitten, interaktiven Diagrammen, Herkunftsseite je Zahl,
  Notizen; HTML-Export; Erfassung mit Beleg (`mi add-*`)
- Neue Extraktoren: schema.org-Organisation (Adressen mit Koordinaten, Marken, Angebote,
  Tätigkeitsgebiet), Länder in Standort-/Vertriebssätzen, Reichweite, Vertriebswege,
  Zielgruppen, Produktüberschriften, Abbau/Einstellungen/Umstrukturierung in Pressetexten;
  Wikidata: Hauptsitz (P159, Koordinaten P625), Produkte (P1056), Marken (P1716),
  Töchter/Mutter (P355/P749), Tätigkeitsgebiet (P2541)

Nachbesserung nach Abgleich mit dem Auftrag (2026-10-20):
- Bericht, Export, Scoring und Funktionsnachweis rechnen Wachstum jetzt mit derselben Logik wie
  Profil und Oberfläche (`analytics.growth` nutzt `intel.timeseries`); vorher konnten sie bei
  Übernahmen, Rumpfjahren oder Lücken abweichende CAGR zeigen
- Mitarbeiterangaben aus Text werden nach Stichtag, Jahresdurchschnitt und Vollzeitäquivalenten
  getrennt; „Konzern“/„group“ im Satz setzt den Konsolidierungskreis
- Historie nennt Kennzahlen deutsch und zeigt bei neuen Zeiträumen die Nettoveränderung zum
  zuletzt bekannten Wert (mit Hinweis auf unbekannte Zwischenjahre)

**Nicht erledigt / ehrlich offen:**
- Teilbranchen werden nur manuell erfasst (`mi add-fact --type sub_industry`), nicht automatisch
- **Weiterhin kein Live-Test** gegen echte Quellen (Netzwerkrichtlinie der Entwicklungsumgebung);
  die Wikidata-Eigenschafts-IDs der Phase 3 sind nach Dokumentationsstand verwendet, nicht live
  geprüft. Abnahme A0 bleibt der nächste Schritt.
- Regelbasierte Profilextraktion ist bewusst vorsichtig und unvollständig: Städte werden nur aus
  strukturierten Daten (schema.org, Wikidata) übernommen, aus Fließtext nur Länder.
  Produktgruppen aus Überschriften haben geringe Sicherheit und brauchen Sichtprüfung.
- Übernahmen/Konsolidierungsänderungen werden aus Pressetexten nur als unbestätigte Hinweise
  erkannt; als Strukturbruch zählen nur erfasste/belegte Ereignisse (`mi add-event`).
- Mitarbeiter nach Land/Standort nur bei manueller Erfassung (`--segment`); keine automatische
  Extraktion von Segmenttabellen aus Geschäftsberichten.
- Keine organische/währungsbereinigte Wachstumsrate (bräuchte Umsätze der Zukäufe und
  Vorjahreskurse je Währungsanteil).
- Oberfläche ohne Anmeldung (nur lokal); Filterlisten, Run-Steuerung und Vergleichsansicht
  der Spezifikation (Bildschirme 1–3, 7–11) fehlen noch.

## 0a. Stand nach Phase 2 – Research Engine (2026-10-19, Version 0.2.0)

Umgesetzt und mit Mock-Daten getestet (**130 automatische Tests grün**, `ruff` und
`mypy --strict` ohne Befund):

- Rechercheaufträge aus Freitext und Filtern (`mi research`), Strategie mit Fachlexikon und
  Herkunft jedes Suchbegriffs; die sieben Beispielaufträge der Anforderung sind als Tests
  hinterlegt (`tests/test_strategy_llm.py`)
- Einheitliche Adapter-Schnittstelle; neun Adapter (GLEIF, Wikidata, SEC EDGAR, Companies
  House, Unternehmenswebseiten inkl. IR-PDFs/Karriere/Presse, TED, GDELT, Eurostat,
  Firmenlisten) – Details und Grenzen in `docs/PROVIDERS.md`
- Acht Schritte als persistente Aufgaben mit Fortschritt, getrennter Kennzeichnung (ok /
  Fehler / übersprungen), Pause/Fortsetzen, Budget (Abrufe, Aufgaben, Zeit, KI-Aufrufe,
  Kosten 0 €), Erkennung bereits untersuchter Quellen, konfigurierbare Rate Limits,
  Wiederholungen und Timeouts
- Deterministische Extraktion (Geld/Skala/Währung/Zeitraum, Mitarbeiter, Impressum-IDs,
  JSON-LD-Stellen, Pressemeldungen, PDF), Plausibilitätsprüfung beim Speichern,
  Dubletten-/Tochter-Kandidaten, Änderungsereignisse, Bericht mit Quellen- und
  Berechnungskennzeichnung
- LLM-Provider-System (keiner / Ollama / OpenAI-kompatibel lokal) mit geprüften KI-Aufgaben

**Nicht erledigt / ehrlich offen:**
- Kein Anbieter ist live getestet; der manuelle Integrationstest
  (`docs/MANUAL_INTEGRATION_TEST.md`) scheiterte in der Entwicklungsumgebung an der
  Netzwerkrichtlinie (GLEIF/Wikidata: `ProxyError 403`) und muss auf einem Rechner mit
  Internetzugang ausgeführt werden (= Abnahme A0)
- Keine allgemeine Websuche (keine kostenlose zulässige API) und kein lizenzierter
  Datenanbieter (kein Vertrag) – Schnittstelle vorhanden
- Eurostat-Datensatzcode und TED-Gewinnerfeld live ungeprüft
- PostgreSQL weiterhin nicht gegen echte Instanz getestet; Web-UI (M6) offen

## 0. Stand nach Prototyp P1 (2026-10-18)

Implementiert und getestet (35 automatische Tests, alle grün; `ruff` und `mypy --strict` ohne
Befund): Domäne, Datenmodell-Kern (SQLite), FetchGateway, Quellenadapter GLEIF, Wikidata,
SEC EDGAR, EZB, TED, GDELT, Webseiten, regelbasierte Extraktion, optionale lokale KI,
Entity Resolution (stark/Kandidat), Profil, Discovery, Analyse, Scoring, Export, pausierbare
Runs, Funktionsnachweis `mi prove`.

### Funktionsnachweis – tatsächliche Ergebnisse

| Umgebung | Ergebnis | Bewertung |
|---|---|---|
| Synthetisches Test-Internet (`tests/test_end_to_end.py`, erfundene Testdaten im Format der Quellen) | 9 von 9 Kriterien | belegt die Verarbeitungskette, **kein** Nachweis echter Recherche |
| Synthetisch, alle Quellen gestört | Kriterien 1 und 9 erfüllt, keine Zahl erfunden, jeder Fehler protokolliert, Status `completed_with_gaps` | belegt sauberes Verhalten bei Ausfall |
| **Realer Lauf** `mi prove` in der Cloud-Entwicklungsumgebung (2026-10-18), Bericht: `docs/proof/2026-10-18_cloud-sandbox_PROOF_REPORT.md` | **2 von 9** (1 und 9) – alle 78 Quellenaufrufe scheiterten mit `ProxyError: 403 Forbidden` der Netzwerkrichtlinie dieser Umgebung; korrekt protokolliert, keine Zahl erfunden, Exporte leer und als „NICHT erfüllt“ gewertet | **realer Nachweis noch offen** (Ursache: Umgebung, nicht Software – aber das ist erst mit A0 belegt) |
| Realer Lauf auf einem PC/Server mit Internetzugang | **ausstehend** – Abnahme A0 unten | |

### Abnahme A0 (nächster Schritt, durch Yellotools/IC-Ware)

Auf einem Rechner mit Internetzugang (Firewall-Freigabe der Hosts aus `mi sources`):

```
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -e .
copy market_intel.example.toml market_intel.toml   (contact_email eintragen)
mi prove
```

Abnahmekriterium: `proof/PROOF_REPORT.md` zeigt 9/9 oder benennt für jedes nicht erfüllte
Kriterium die konkrete Ursache (Quelle, Status, Fehlermeldung). Erwartbar: Kriterium 5/6
hängen davon ab, ob Wikidata/SEC für die Vergleichsfirmen mehrjährige Werte liefern; das
Ergebnis wird so dokumentiert, wie es ist.

## 1. Offene Fragen an Yellotools

| # | Frage | benötigt für |
|---|---|---|
| Q1 | Einzelplatz-Pilot ausreichend? Wie viele Nutzer später? | M6, M15 |
| Q2 | Eigener Server/VM vorhanden (für PostgreSQL und Web-UI im LAN)? | M15 |
| Q3 | Firewall: Freigabe der Quellen-Hosts möglich? | A0 |
| Q4 | Lokale KI gewünscht? Hardware (RAM, GPU) des Zielrechners? | M8b |
| Q5 | Artikelstamm-Export und Ansprechpartner Vertrieb (Kategorien, Anwendungsfälle validieren) | M10 |
| Q6 | Firmenliste bestehender Kunden (Name, Land, Branche) für Ausschluss und Scoring-Prüfung | M12 |
| Q7 | Zielländer/-branchen für den Pilot | M7, M9 |
| Q8 | Datenschutz-/Rechtsprüfung der Quellen (Register-Status) | Gate L |

## 2. Gates

- **Gate L (Recht):** Quelle erst aktiv, wenn `legal_status` im Register freigegeben ist.
- **Gate Kosten:** keine verpflichtenden Kosten (siehe oben).
- **Gate P (Pilot):** nach M13, 4 Wochen mit 1–3 Nutzern.

## 3. Meilensteine

Status: ✅ im Prototyp erledigt · 🟡 teilweise · ⬜ offen. Aufwand in Personentagen (PT), grob.

| MS | Inhalt | Status | Abnahmekriterien | Tests |
|---|---|---|---|---|
| M0 | Gerüst, `pyproject`, ruff, mypy strict, pytest | ✅ | Werkzeuge ohne Befund | – |
| M0b | CI (GitHub Actions, kostenlos für öffentliche Repos bzw. im Freikontingent; alternativ lokal), Hash-Sperrdatei, `pip-audit`, `import-linter`, PostgreSQL-Service in CI | ⬜ 2 PT | CI grün auf SQLite **und** PostgreSQL; Schichtverletzung lässt CI scheitern | Matrix-Tests |
| M1 | Domäne + Datenmodell-Kern, append-only, gültiger Wert | ✅ (SQLite) / 🟡 (PostgreSQL ungetestet) | Update/Delete von Beobachtungen scheitert; Ableitungsregeln | `test_observations_are_append_only`, `test_official_wins_over_company_reported`, Konflikt-Tests |
| M1b | Alembic-Migrationen, Sicherung vor Migration, restliche Tabellen aus `DATA_MODEL.md` (Watchlist, UserNote, CountryMarket, Hiring/Layoff, ChangeEvent, Review) | ⬜ 5 PT | Migration leer→aktuell und n→n+1 auf beiden DBs | Migrationstests |
| M2 | FetchGateway | ✅ | gesperrte Quelle ohne Netz; SSRF-Suite; Cache; Retry-After; Größenlimit; Protokoll jedes Aufrufs | `tests/test_gateway.py` |
| M3 | Runs/Aufgaben, Pause/Fortsetzen | ✅ (ein Prozess) | Strg+C → `paused`; `resume` setzt fort, keine Duplikate | `test_pause_and_resume` |
| M3b | Hintergrund-Worker mit Leases, Zeitpläne, mehrere Worker (PostgreSQL `SKIP LOCKED`) | ⬜ 5 PT | Worker-Abbruch → Übernahme ohne Doppelergebnis | Chaos-Test |
| M4 | GLEIF, Wikidata, EZB | ✅ | starke IDs, Mutterbeziehungen, Reihen mit Stichtag, Lücken statt Fehler | E2E-Test |
| M5 | Entity Resolution v1 | 🟡 | nur starke IDs automatisch ✅; **offen:** Goldstandard ≥ 300 Paare, 0 falsche Merges, Kandidaten-Prüfliste mit Merge/Unmerge | `test_name_normalization`; Goldstandard ⬜ |
| M6 | Web-UI lokal (FastAPI/Jinja2/HTMX, alle MIT/BSD) | ⬜ 10 PT | jeder Wert in ≤ 2 Klicks zu Quelle/Zitat; nur 127.0.0.1; Run starten/pausieren | Playwright-E2E |
| M7 | Strukturierte Kennzahlen: SEC ✅; Companies House (kostenloser Schlüssel), Eurostat, Destatis ⬜ | 🟡 6 PT | Abgleich 10 UK-Firmen gegen Originaldokumente; Statistik je WZ/NACE mit Bruchkennzeichnung | Vertragstests |
| M8 | Dokument-Extraktion: HTML ✅; **PDF-Upload** von Jahresabschlüssen (freie Bibliothek, z. B. pypdf BSD-3) ⬜ | 🟡 8 PT | Eval ≥ 50 Dokumente: 0 Werte ohne geprüftes Zitat, Präzision ≥ 95 % | Eval-Suite |
| M8b | Lokale KI: Schnittstelle ✅; Messung auf Zielhardware, Prompt-Injection-Korpus ⬜ | 🟡 3 PT | Laufzeit je Seite auf Zielrechner gemessen und dokumentiert; Injection-Korpus ohne unbelegte Ergebnisse | Korpus-Tests |
| M9 | Discovery-Erweiterung: CSV-Import eigener Firmenlisten, Impressum-Parser (Registernummer, USt-IdNr.), Common-Crawl-Seitenlisten (Bedingungen prüfen) | ⬜ 6 PT | ≥ 90 % Registernummern aus 30 Impressen korrekt | Impressum-Korpus |
| M10 | Artikelstamm-Import, Anwendungsfälle vom Vertrieb validiert | 🟡 (Hypothesen-Datei ✅) 4 PT | echte Kategorien, ≥ 5 validierte Anwendungsfälle | Import-Tests |
| M11 | Signale: TED ✅, GDELT ✅; Karriereseiten-Auswertung (aggregiert) ⬜ | 🟡 4 PT | keine Personendaten in Signalen (Scan-Test) | Scan-Test |
| M12 | Scoring v1 | ✅ Entwurf / ⬜ Validierung | reproduzierbar ✅; Renormierung ✅; **offen:** Rückblicktest mit Bestandskunden | `test_scoring_renormalizes_and_flags_missing_data` |
| M13 | Analytics & Export: Vergleich, CAGR nur bei Vergleichbarkeit, CSV/XLSX/JSON/HTML ✅; Aufbewahrungsregel Rohdokumente, Zitat-Bereinigung ⬜ | 🟡 3 PT | Export je Zahl mit Quelle/Zeitraum/Status ✅; Formel-Injektion ✅ | Export-Tests |
| M14 | Watchlists, Änderungserkennung, Benachrichtigung in der App | ⬜ 5 PT | genau ein Änderungsereignis bei geänderter Kennzahl | Fixture-Tests |
| M15 | Teambetrieb selbst gehostet: PostgreSQL, Web-UI im LAN/VPN, OIDC (vorhandenes Entra ID oder Keycloak), Rollen, Backup/Restore | ⬜ 8 PT | Rollenmatrix grün; Wiederherstellung getestet | Rollen-/Restore-Tests |
| M16 | Verteilung: Windows-Start ohne Installation (venv-Skript) bzw. PyInstaller-Paket; Update-Anleitung | ⬜ 4 PT | Neuinstallation und Update auf sauberem Windows dokumentiert | Installationsprotokoll |

Summe offen: ca. 73 PT (ohne Puffer; nach A0 neu schätzen).

## 4. Teststrategie

| Ebene | Werkzeug | Läuft |
|---|---|---|
| Unit/Integration (SQLite) | pytest | immer |
| Synthetisches Test-Internet | `httpx.MockTransport` (`tests/fakeweb.py`) | immer; Ergebnisse sind **keine** Recherche-Nachweise |
| Realer Lauf | `mi prove` | manuell bzw. geplant (nie im Standard-CI, um Quellen nicht zu belasten) |
| PostgreSQL | CI-Service-Container | ab M0b |
| E2E Web-UI | Playwright | ab M6 |

## 5. Nächste Schritte

1. **A0** auf einem Rechner mit Internetzugang ausführen und Bericht ins Repository legen.
2. M0b (CI, PostgreSQL-Test), M5-Goldstandard, M1b-Migrationen.
3. Mit dem Vertrieb Anwendungsfälle validieren (Q5) und Zielmärkte festlegen (Q7).
