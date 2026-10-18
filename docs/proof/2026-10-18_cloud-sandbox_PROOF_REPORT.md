# Funktionsnachweis Market Intelligence (kostenloser Betrieb)

Run 1 · Umgebung: Linux 6.18.44-fc-v139, Python 3.13.16, market-intel 0.1.0

**Ergebnis: 2 von 9 Kriterien erfüllt.**

| # | Kriterium | Ergebnis | Belege |
|---|---|---|---|
| 1 | Start ohne bezahlte API-Schlüssel | erfüllt | Aktive Quellen: gleif, wikidata, sec_edgar, ecb_fx, ted, gdelt, website. Kostenpflichtige Quellen aktiv: keine. Gelesene Schlüssel: keine (das Programm liest keine Bezahl-Schlüssel). Hinweis: Umgebungsvariablen mit Anbieternamen vorhanden, aber ungenutzt: ['ANTHROPIC_BASE_URL'] |
| 2 | Zulässige öffentliche Internetquellen erreicht | NICHT erfüllt | Erfolgreiche Abrufe je Quelle: keine; gescheiterte/gesperrte: [('gdelt', 'network_error', 6), ('gleif', 'network_error', 30), ('ted', 'network_error', 12), ('wikidata', 'network_error', 30)] |
| 3 | Unternehmen gefunden | NICHT erfüllt | 0 Unternehmen in der Datenbank |
| 4 | Quellen und Kennzahlen gespeichert | NICHT erfüllt | 0 Quelldokumente, 0 Kennzahlen-Beobachtungen in diesem Lauf |
| 5 | Historische Daten analysiert | NICHT erfüllt | keine Reihe mit ≥ 2 vergleichbaren Werten gefunden |
| 6 | Unternehmensvergleich erzeugt | NICHT erfüllt | 0 Firmen im Vergleich, 0 mit mindestens einer Kennzahl |
| 7 | Potenzielle Kunden über Anwendungsfälle gefunden | NICHT erfüllt | Fit-Bewertungen mit Beleg: keine |
| 8 | Ergebnisse exportiert | NICHT erfüllt | Dateien: vergleich.csv, chancen.csv, kennzahlen.csv, firmen.csv, signale.csv, luecken.csv, quellenaufrufe.csv, quellen.csv, market_intel_export.xlsx, market_intel_export.json, bericht.html; Datenzeilen: {'vergleich.csv': 0, 'chancen.csv': 0, 'kennzahlen.csv': 0} |
| 9 | Funktioniert ohne lokales KI-Modell | erfüllt | KI-Schnittstelle während des Laufs: none |

## Hinweise des Laufs (Quellenfehler, Kandidaten, Lücken)

- GLEIF-Suche „3M“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „3M“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „3M“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- „3M“: keine Firma eindeutig gefunden
- GLEIF-Suche „Avery Dennison“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „Avery Dennison“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „Avery Dennison“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- „Avery Dennison“: keine Firma eindeutig gefunden
- GLEIF-Suche „ORAFOL“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „ORAFOL“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „ORAFOL“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- „ORAFOL“: keine Firma eindeutig gefunden
- GLEIF-Suche „Fahrzeugfolierung“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Folierung“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Car Wrapping“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Werbetechnik“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „Werbetechnik“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „sign industry“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- TED-Suche „Fahrzeugbeschriftung“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- TED-Suche „Folierung“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GDELT „Fahrzeugfolierung“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Sonnenschutz“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Glasfolien“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GLEIF-Suche „Fensterfolien“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „Glaserei“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- Wikidata-Suche „glazing“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- TED-Suche „Sonnenschutzfolie“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- TED-Suche „Fensterfolie“ fehlgeschlagen: network_error ProxyError: 403 Forbidden
- GDELT „Sonnenschutzfolie“ fehlgeschlagen: network_error ProxyError: 403 Forbidden

## Abrufstatistik

```
{
 "gleif": {
  "network_error": 30
 },
 "wikidata": {
  "network_error": 30
 },
 "ted": {
  "network_error": 12
 },
 "gdelt": {
  "network_error": 6
 }
}
```
