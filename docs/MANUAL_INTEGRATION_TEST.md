# Manueller Integrationstest (reale Unternehmen, öffentliche Quellen)

Automatisierte Tests nutzen ausschließlich Mock-Daten (`tests/fakeweb.py`). Dieser Test prüft
die echte Anbindung. Er braucht Internetzugang zu den Quellen und kostet nichts.

## Voraussetzungen

- Python ≥ 3.12, `pip install -e ".[dev]"`
- Firewall/Proxy erlaubt: api.gleif.org, www.wikidata.org, query.wikidata.org, www.sec.gov,
  data.sec.gov, data-api.ecb.europa.eu, api.ted.europa.eu, api.gdeltproject.org,
  ec.europa.eu sowie die Websites der Unternehmen (hier z. B. www.orafol.com, www.3m.com)
- Kontaktadresse für den User-Agent (SEC und Wikimedia verlangen sie)

## Variante A: pytest

```bat
set MI_LIVE=1
set MI_CONTACT_EMAIL=ihre.adresse@firma.de
pytest tests/manual -m live -s
```

## Variante B: Kommandozeile

```bat
mi research "Vergleiche die Unternehmensentwicklung von „3M“ und „ORAFOL“ über die letzten fünf Jahre" --country DE --out live-out
mi status 1
```

## Abnahmecheckliste (vom Prüfer auszufüllen)

| # | Prüfung | Erwartung | Ergebnis |
|---|---|---|---|
| 1 | `mi status <id>` | alle 8 Schritte durchlaufen; Fehler einzeln ausgewiesen | |
| 2 | 3M: LEI, Wikidata-QID, SEC-CIK in `bericht.md` | Kennungen mit [Qn] | |
| 3 | 3M: Umsatzreihe | ≥ 3 Geschäftsjahre aus SEC (official), EUR-Werte als [Berechnung] | |
| 4 | ORAFOL: LEI/Sitz | aus GLEIF, wenn LEI vorhanden; sonst „nicht gefunden“ dokumentiert | |
| 5 | ORAFOL: Umsatz/Mitarbeiter | nur mit Zitat und Zeitraum; sonst „unbekannt (gesucht in …)“ | |
| 6 | Stichprobe: 3 Zahlen aus `kennzahlen.csv` an der Original-URL nachprüfen | Zitat steht in der Quelle, Wert und Zeitraum stimmen | |
| 7 | `quellenaufrufe.csv` | jeder Abruf mit Status; gesperrte (robots/TDM) korrekt markiert | |
| 8 | Abbruch mit Strg+C in Schritt 3, dann `mi resume <id>` | setzt fort, keine doppelten Werte | |

Ergebnis und Datum hier eintragen und den Ordner `live-out/` (ohne Datenbank) archivieren.

## Stand in der Entwicklungsumgebung

Am 2026-10-19 war der Test in der Cloud-Entwicklungsumgebung nicht ausführbar: Die
Netzwerkrichtlinie der Umgebung beantwortet jede Verbindung zu den Quellen mit
`403 Forbidden` (Proxy). Der Test ist daher **noch nicht real bestanden**; er muss auf einem
Rechner mit Internetzugang ausgeführt werden.
