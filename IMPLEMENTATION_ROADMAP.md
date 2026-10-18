# Umsetzungsplan – Yellotools Market Intelligence

Stand: 2026-10-18 · Revision 2 (verbindlich: **100 % kostenloser Betrieb**)

Grundsätze: kleine, abnehmbare Meilensteine; jeder endet mit grünen Tests. **Gate „Kosten“
gilt für jeden Meilenstein: keine neue Abhängigkeit, Quelle oder Infrastruktur mit
verpflichtenden Kosten** – Prüfung über `docs/DEPENDENCIES_AND_COSTS.md`.

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
