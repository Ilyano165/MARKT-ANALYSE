# Live-Test Phase 5 – Protokoll

Datum: 2026-10-23 · Umgebung: Cloud-Sandbox (ausgehender Verkehr über den Proxy der Umgebung)
· Kontakt im User-Agent: `MI_CONTACT_EMAIL` gesetzt.

Alle Angaben unten wurden tatsächlich ausgeführt und gemessen. Firmenbezogene Aussagen stammen
ausschließlich aus den abgerufenen Dokumenten (Zitate in `live-out/`, nicht eingecheckt).

## Teil A – bis 20:32 UTC: alle Hosts gesperrt

GLEIF, Wikidata, SEC, yellotools.com und Firmenwebseiten: Proxy antwortete `CONNECT` mit 403.
Zwei Versuche, beide Live-Tests fehlgeschlagen (0 Firmen, 0 Dokumente, 0 Bewertungen – **keine
erfundenen Ergebnisse**). Behoben: Proxy-Sperren wurden dreimal mit Wartezeit wiederholt → jetzt
ein Versuch (18 → 6 Abrufe, ~25 s → 5 s), Test `test_proxy_block_is_not_retried`.

## Teil B – ab 20:40 UTC: Netz teilweise offen

### B1. Quellen einzeln (curl und App, mit echtem User-Agent)

| Quelle | Ergebnis | Behandlung |
|---|---|---|
| GLEIF `api.gleif.org` | **funktioniert** (200, Golden Copy vom 2026-10-23) | genutzt |
| Wikidata `www.wikidata.org` | **gesperrt**: 403 „Please respect our robot policy“ für httpx-Clients; vereinzelt 429 mit `Retry-After: 28`. Auch mit User-Agent nach Wikimedia-Policy (`Client/Version (Kontakt) httpx/Version`) 403. | respektiert, kein Nachahmen anderer Clients; Host für den Lauf gesperrt |
| yellotools.com (inkl. `/robots.txt`, `/shop/`) | **gesperrt**: 403 mit `cf-mitigated: challenge` (Cloudflare-Bot-Abwehr) | respektiert, nicht umgangen |
| GDELT | **gedrosselt**: 429 „one every 5 seconds“, obwohl Abrufe 13–15 s auseinander lagen (Limit gilt offenbar für die geteilte Ausgangs-IP) | Host nach 2 gescheiterten Abrufen in Folge für den Lauf ausgesetzt |
| TED | funktioniert (31 Abrufe ok) | genutzt |
| Firmenwebseiten | orafol.com, umdasch.com, steinel.de, db.com, graphics.averydennison.com: **funktionieren**, robots.txt eingehalten; **sixt.de: 403** | Sixt respektiert → keine Website-Belege |

### B2. Gefundene und behobene Fehler (je Regressionstest in `tests/test_phase5_regressions.py`)

| # | Live-Befund | Ursache | Korrektur | Test |
|---|---|---|---|---|
| 1 | „ORAFOL“ und „3M“ nicht aufgelöst, obwohl GLEIF „ORAFOL Europe GmbH“ lieferte | Namensähnlichkeit 0,63 < 0,85 | Name + nur Länder-/Konzernzusatz (Europe, Deutschland, Holding …) gilt als Treffer, Abweichung wird im Ergebnis genannt; Tätigkeitswörter („Sixt Leasing“, „green energy“) nicht | `…country_suffix`, `…activity_suffix_is_not_accepted` |
| 2 | „Sixt SE“ und „DEUTSCHE BANK AKTIENGESELLSCHAFT“ nicht unter den ersten zehn GLEIF-Treffern | Volltextsuche rankt nach allen Feldern | Suche im Rechtsnamen (`filter[entity.legalName]`) | (über 1, 3, 4) |
| 3 | Umdasch Group (AT) bei Auftrag mit Land DE nicht gefunden | Länderfilter auch für genannte Firmen | Ausweichsuche weltweit | `…foreign_seat_is_found` |
| 4 | Mehrere gleichnamige Gesellschaften (Avery Dennison B.V., S.R.L, CORPORATION …) | — | nur über gleichnamige oberste Konzernmutter auflösen, sonst „mehrdeutig, nicht übernommen“ (kein Raten) | `…via_common_parent` |
| 5 | Wikidata nach 403 noch fünfmal angefragt | Sperre galt nur je Abruf | 401/403/Bot-Abwehr sperrt den Host für den Rest des Laufs (protokolliert) | `test_denied_host_is_not_contacted_again` |
| 6 | GDELT 429 bei jeder Anfrage, ~40 s je Begriff | Wiederholung bei dauerhafter Drosselung | nach 2 an 429 gescheiterten Abrufen in Folge Host aussetzen | `test_persistent_rate_limit_suspends_host` |
| 7 | Auftrag mit Firmenliste: 509 Suchtreffer verdrängten alle 6 Listenfirmen | Liste wie freie Suche gewichtet | Listenfirmen (Rolle `listed`) vorrangig wie genannte Firmen | `test_listed_companies_take_precedence_over_discovery` |
| 8 | Steinel Fit 40, Priorität 52 – einziger „Beleg“: Markenhinweis im Impressum („…geschützt. Dies gilt insbesondere für STEINEL Marken, Typenschilder, Firmenlogos und Embleme.“) | Rechtstexte als Tätigkeitsbeleg gewertet | Impressum/Legal Notice/Datenschutz/AGB/Cookies zählen nicht | `test_legal_notice_is_no_activity_evidence` |
| 9 | ORAFOL: Menüeintrag „Banners“ als 6 Belege (je Seite), hob Quellenqualität | identischer Satz je Seite einzeln gezählt | wörtlich gleicher Satz zählt einmal | `test_repeated_navigation_text_counts_once` |
| 10 | User-Agent nicht im Wikimedia-Format | — | `YellotoolsMarketIntel/0.4.0 (mailto:…) httpx/0.28.1` | `test_user_agent_follows_wikimedia_format` |

Gegenprobe ohne Korrektur tatsächlich ausgeführt für 7, 8 und 9 (schlagen fehl: `{3} == {1}`, 2 Impressum-Treffer, 7 statt 2 Belege); für 1–6 und 10 nicht separat ausgeführt.

### B3. Ende-zu-Ende mit sechs realen Firmen (finaler Lauf 21:3x UTC, 11 min, beide Live-Tests bestanden)

Ablauf: (1) Firmen per GLEIF auflösen, (2) offizielle Websites als Nutzereingabe per Firmenliste
(Abgleich per LEI, **keine Dublette**), (3) Websites abrufen (robots.txt), (4) Belege mit
Verneinungsprüfung, (5) Bewertung mit unverändertem Modell v2.

| Firma | Auflösung (GLEIF) | Website | Ergebnis | Belege (wörtlich, eigene Website) |
|---|---|---|---|---|
| ORAFOL | ORAFOL Europe GmbH, 39120074OIJQ5NWZSZ24 (Name mit Zusatz, offengelegt) | ok | Fit 53, Priorität 62, „Mittlerer Produkt-Fit, interessante Expansion“ | Menü: „Banners“, „Polycarbonate films“ |
| Avery Dennison | **mehrdeutig** (B.V. NL / CORPORATION US) → im Test manuell LEI 549300PW7VPFCYKLIV37 festgelegt | ok | Fit 81, Priorität 70, „Hoher Produkt-Fit“ | „Paint Protection Films“, Pressemitteilung zum Launch einer PPF |
| Sixt | Sixt SE, 5299004ZME6CSBR7WP07 | **403** (respektiert) | Fit 0 – *keine Daten*, nicht „kein Bedarf“ | – |
| Umdasch Group | Umdasch Group AG, 529900JXJZCW0P7BOI46 (AT, über weltweite Suche) | ok | Fit 80, Priorität 80, „Hoher Produkt-Fit, Wachstum unklar“ | „Ladenbau“, „Store Design“, „Logistik, Rollout und Montage“, Stellenanzeige „Projektleiter … Ladenbau“ |
| Steinel | Steinel GmbH, 529900T6TFV0XNI6N125 | ok | Fit 0 (vor Korrektur 8: Fit 40) | – |
| Deutsche Bank (Negativfall) | DEUTSCHE BANK AKTIENGESELLSCHAFT, 7LTWFZYICNSX8D621K86 | ok | **Fit 0, Priorität 0** – erwartet | – |

Jedes Zitat wurde vom Test gegen den gespeicherten Dokumenttext geprüft (Assertion).

### B4. Was belegt ist und was nicht

- **Belegt**: Rechtsnamen, LEIs, Sitzländer (GLEIF); die zitierten Sätze stehen auf den
  offiziellen Websites (Abrufdatum im Abrufprotokoll).
- **Grenze der Bewertung (nicht behoben, bewusst)**: Bei Avery Dennison und ORAFOL sind die
  Treffer Produktnamen von **Materialherstellern** (Folien), keine Belege für das Verarbeiten von
  Folien. Die Gegenindizien („Folienhersteller“, „film manufacturer“) stehen auf den abgerufenen
  Seiten nicht wörtlich; ein Gegenindiz zu unterstellen wäre erfunden. Hoher Fit heißt hier
  „Folienbranche“, nicht „Anwender“ → Vertrieb muss als Partner/Kanal oder Hersteller einordnen
  (Status „verworfen“ bzw. Watchlist-Kommentar).
- **Unsicher**: Sixt (keine Daten), Avery-Gesellschaft (manuell gewählt), alle
  Anwendungsfälle bleiben Hypothesen; Wachstum überall „unklar“ (keine Kennzahlen in den Quellen,
  Wikidata gesperrt, SEC nicht abgefragt für diese Firmen).

### B5. Produktmatrix

Abgleich mit yellotools.com **nicht möglich** (Bot-Abwehr). Eine Websuche nannte sinngemäß die
vorhandenen Gruppen und zusätzlich Werkzeugwände/Lagerregale; das ist keine wörtliche
Herstellerquelle → **keine Änderung**, alle Gruppen bleiben `researched`
(`products.toml`, `meta.verification`).

## Tests

193 Tests: 191 bestanden, 2 übersprungen (Live-Tests ohne `MI_LIVE=1`), 0 fehlgeschlagen;
10 davon neu in diesem Durchgang (insgesamt 15 Phase-5-Regressionstests). Live: 2 von 2 bestanden. `ruff`, `mypy --strict` ohne Befund.

## Nächste Schritte

1. Produktdaten von Yellotools direkt (Export/Katalog-PDF) oder Freigabe für den Abruf –
   dann Produktmatrix auf `verified` heben.
2. Website-Quelle ohne Wikidata: Websites per Liste pflegen, oder Wikidata lokal/mit
   Wikimedia-Konto (OAuth) bzw. per Dump nutzen (laut Robot-Policy vorgesehen).
3. Hersteller-/Händler-Erkennung aus der Rolle im Text (z. B. „we manufacture“, „Hersteller
   von“) als zusätzliches Gegenindiz – erst nach Prüfung echter Formulierungen.
