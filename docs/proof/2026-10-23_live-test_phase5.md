# Live-Test Phase 5 – Protokoll

Datum: 2026-10-23 · Stand vor dem Test: Commit `dc28e83` · Umgebung: Cloud-Sandbox
(ausgehender Verkehr über den Proxy der Umgebung)

## 1. Erreichbarkeit der Quellen (tatsächlich ausgeführt)

| Quelle | Prüfung | Ergebnis |
|---|---|---|
| GLEIF `api.gleif.org` | `curl` und Abruf über `FetchGateway` (Live-Test) | **gesperrt**: Proxy antwortet `CONNECT` mit 403 („policy denial“); App: `ProxyError: 403 Forbidden` |
| Wikidata `www.wikidata.org`, `query.wikidata.org` | `curl` und Live-Test | **gesperrt**, gleicher Fehler |
| `www.yellotools.com`, `yellotools.com` | `curl`, Abruf über `FetchGateway` (Quelle `website`), Web-Abrufwerkzeug | **gesperrt** (Proxy 403); Web-Abrufwerkzeug: DNS `ENOTFOUND` |
| Firmenwebseite (Stichprobe `www.orafol.com`) | `curl` | **gesperrt** |

**Zweiter Versuch 2026-10-23 20:32 UTC** (auf Wunsch wiederholt): unverändert gesperrt –
`curl` auf GLEIF, Wikidata (API und SPARQL), SEC, yellotools.com, orafol.com: keine Verbindung
(Proxy 403); Web-Abrufwerkzeug: DNS `ENOTFOUND` für api.gleif.org und www.yellotools.com; beide
Live-Tests erneut ausgeführt und fehlgeschlagen (0 Firmen, 0 Dokumente, je URL ein Versuch).

Kein einziger Live-Abruf war erfolgreich. Es wurden daher **keine echten Unternehmensdaten**
abgerufen, gespeichert oder bewertet.

## 2. Verhalten der Anwendung bei gesperrten Quellen (gemessen)

`MI_LIVE=1 pytest tests/manual -m live` (beide Live-Tests ausgeführt, beide **fehlgeschlagen**,
weil keine Firma aufgelöst werden konnte):

| Lauf | Abrufe | Versuche je URL | Dauer bis Abbruch | Firmen / Kennzahlen / Bewertungen / Dokumente |
|---|---|---|---|---|
| vor der Korrektur (3M, ORAFOL) | 18 (GLEIF 6, Wikidata 12) | 3 | ca. 25 s | 0 / 0 / 0 / 0 |
| nach der Korrektur (3M, ORAFOL) | 6 (GLEIF 2, Wikidata 4) | 1 | 5 s | 0 / 0 / 0 / 0 |
| Opportunity-Ende-zu-Ende (6 Firmen) | 18 (GLEIF 6, Wikidata 12) | 1 | wenige Sekunden | 0 / 0 / 0 / 0 |

- Jeder Fehlversuch steht mit URL, Zeitstempel, Versuch und Fehlertext im Abrufprotokoll
  (`fetch_log`); der Lauf endet mit `completed_with_gaps`, der Bericht nennt die gescheiterten
  Schritte („alle Quellenabrufe gescheitert“).
- **Keine erfundenen Ergebnisse**: In allen Läufen 0 Firmen, 0 Beobachtungen, 0 Bewertungen.

## 3. Gefundener und behobener Fehler

**Proxy-Sperre wurde wie ein vorübergehender Netzfehler wiederholt** (3 Versuche mit
Wartezeit je URL). Bei gesperrten Hosts verdreifachte das Abrufe und Laufzeit ohne Aussicht auf
Erfolg. Korrektur in `http/gateway.py`: `httpx.ProxyError` mit 403/407 → ein Versuch, Fehlertext
„Netzwerkrichtlinie/Proxy sperrt den Host – keine Wiederholung“. Regressionstest
`test_proxy_block_is_not_retried` (schlägt ohne Korrektur nachweislich fehl).

## 4. Produktmatrix

Abgleich mit yellotools.com **nicht möglich** (siehe 1). Keine Produktangabe geändert; alle
Gruppen bleiben `researched` (nicht verifiziert). Vermerk in `products.toml` (`meta.verification`).

## 5. Ende-zu-Ende mit realen Unternehmen

Vorbereitet und ausgeführt (`test_real_companies_opportunity_end_to_end`), aber **nicht
erfolgreich**, weil keine Quelle erreichbar war. Ausgewählte Firmen (Branchenzuordnung ist eine
Testhypothese, kein Ergebnis): ORAFOL (Folienhersteller), Avery Dennison (Folienhersteller),
Sixt (Autovermietung – Entfolierungs-Hypothese), Umdasch Group (Ladenbau), Steinel
(Heißluftgeräte – Wettbewerbs-/Partnerfall), Deutsche Bank (Negativbeispiel). Über diese Firmen
werden **keine** Tätigkeiten, Zahlen oder Standorte behauptet.

## 6. Ohne Netz geprüft (Regressionstests, alle grün)

- Proxy-Sperre ohne Wiederholung; gesperrte Quellen erzeugen keine Daten und keine Leads
- Bestandskunden-Zuordnung über LEI und über Website-Domain ohne Duplikat (auch beim Wiederholungsimport)
- Bestandskunden und verworfene Firmen nicht im Lead-Export
- Verworfene Firma bei späterer Recherche ausgeschlossen, bei ausdrücklicher Nennung untersucht,
  aber weiter im Segment „verworfen“
- Verneinte Tätigkeit ohne Fit; „nicht nur … sondern auch“ keine Verneinung (bestehende Tests)

## 7. Nächster Schritt

Netzwerkzugang der Umgebung freigeben (Einstellungen der Umgebung → Network access; Hosts siehe
README „Firmennetz“ plus `www.yellotools.com` und die Firmenwebseiten, praktisch eine
uneingeschränkte Stufe) **oder** lokal ausführen:

    export MI_LIVE=1 MI_CONTACT_EMAIL=…
    pytest tests/manual -m live -s

Erst danach sind Live-Abnahme (A0), Produktmatrix-Abgleich und Ende-zu-Ende-Bewertung realer
Firmen möglich.
