# Recherchequellen – Yellotools Market Intelligence

Stand: 2026-10-18 · Revision 2 (verbindlich: **nur kostenlose, rechtlich nutzbare Quellen**)

Jede Quelle steht im Quellenregister (`src/market_intel/sources/registry.py`, Anzeige mit
`mi sources`). Abgerufen wird nur, was `legal_status` `approved`/`approved_with_conditions`
hat, implementiert und aktiviert ist. Die vollständige Kostenübersicht steht in
`docs/DEPENDENCIES_AND_COSTS.md`.

**Prüfstand:** **P** = auf Primärquelle des Anbieters gestützt · **S** = nur Sekundärquellen;
vor produktivem Einsatz gegen die aktuellen Bedingungen prüfen. Alle Angaben: Recherche per
Websuche am 2026-10-18. Aus der Entwicklungsumgebung waren die Quellen selbst nicht erreichbar
(Netzwerkrichtlinie), Formate wurden nach Dokumentation implementiert und mit synthetischen
Testdaten geprüft. Der reale Nachweis erfolgt mit `mi prove` (siehe README).

> **Kostenlos heißt nicht unbegrenzt.** Freie Quellen haben Abruflimits, decken nicht jedes
> Unternehmen ab und liefern für deutsche KMU nur selten Umsatz oder Mitarbeiterzahlen. Die
> Anwendung dokumentiert jede Lücke, statt sie zu schätzen.

## 1. Im Kern implementiert und standardmäßig aktiv (0 €, ohne Registrierung)

| Quelle | Liefert | Kostenloser Umfang / Limits | Lizenz / Bedingungen | Speicherung | Prüfstand |
|---|---|---|---|---|---|
| **GLEIF LEI API** | LEI, rechtlicher Name, Sitz, Registernummer, Mutter (direkt/oberste, Konsolidierung) | frei, ohne Schlüssel; Drosselung im Programm 1 Abruf/s | laut Wiederverwendern CC0 1.0; GLEIF „LEI Data Terms of Use“ | volle Antwort | S (Lizenz), P (Bedingungsseite existiert) |
| **Wikidata** (Action API, EntityData, SPARQL) | QID, Website, Land, Branche, LEI, SEC-CIK, Mitarbeiter- und Umsatzreihen mit Stichtag | frei; SPARQL 60 s Rechenzeit/Minute je Client; User-Agent mit Kontakt Pflicht; seit 05/2025 getrennter „scholarly“-Graph (für Firmen ohne Belang) | CC0 1.0 (Daten); Wikimedia API Usage Guidelines | volle Antwort | S |
| **SEC EDGAR** (`company_tickers.json`, XBRL `companyfacts`) | Jahresumsätze US-börsennotierter Firmen (Konzern), ggf. Mitarbeiter | frei; max. 10 Anfragen/s; User-Agent mit Firma + E-Mail Pflicht | US-Regierungswerk (gemeinfrei) | volle Antwort | P |
| **EZB Data Portal** (EXR, Jahresdurchschnitt) | Wechselkurse für abgeleitete EUR-Werte | frei, ohne Schlüssel | Wiederverwendung mit Quellenangabe „Source: ECB statistics“ (laut Dritt-Doku) | volle Antwort | S |
| **TED Search API v3** | EU-Ausschreibungen (Titel, Auftraggeber, Land, Datum, CPV) | frei, anonym; max. 100 Treffer/Seite | Wiederverwendung mit Quellenangabe (Beschluss 2011/833/EU, laut Dritt-Doku) | volle Antwort | P (anonymer Zugang), S (Lizenz) |
| **GDELT DOC 2.0** | Nachrichtenmeldungen (Titel, URL, Datum, Domain) | frei, ohne Schlüssel; gedrosselt, Programm 1 Abruf/5 s | laut GDELT frei für jede Nutzung mit Zitat + Link; Artikeltexte gehören den Verlagen → nur Metadaten gespeichert | Metadaten | S |
| **Unternehmenswebseiten** | Selbstangaben (Mitarbeiter), Leistungen → Anwendungsfall-Belege | max. 8 Seiten/Domain (einstellbar), 1 Abruf/2 s je Host | robots.txt verbindlich; TDM-Vorbehalt (§ 44b UrhG) → kein Abruf/keine Speicherung; nur Zitate ≤ 1 000 Zeichen als Beleg | Seite + Belegzitat | – |

## 2. Optional kostenlos (Registrierung nötig, standardmäßig aus)

| Quelle | Voraussetzung | Stand |
|---|---|---|
| **UK Companies House API** | kostenloses Konto + kostenloser Schlüssel (`MI_COMPANIES_HOUSE_KEY`); 600 Anfragen/5 min (P) | im Register, Adapter folgt in M7 |
| **Destatis GENESIS-Online** | Daten frei (DL-DE BY 2.0, P); API-Token laut Dritt-Doku über kostenloses Konto (S) | geplant M7 (Marktgrößen je WZ) |
| **Eurostat API** | frei, ohne Schlüssel; ab Berichtsjahr 2025 NACE Rev. 2.1 (Zeitreihenbruch) | geplant M7 |
| **Nationale Register mit EU-High-Value-Datasets** (z. B. KvK NL, CRO IE, CC BY 4.0, P) | je Land prüfen | ab M9 je Zielmarkt |

Fehlt eine dieser Quellen, arbeitet die Anwendung ohne sie weiter.

## 3. Ersatz für eine kostenpflichtige Such-API

Geprüft, ob es eine **kostenlose und für kommerzielle Nutzung zulässige** allgemeine
Websuche gibt:

| Option | Ergebnis der Prüfung | Entscheidung |
|---|---|---|
| Google Custom Search JSON API | für Neukunden geschlossen, Abschaltung 01.01.2027 (S, mehrere Quellen) | ausgeschlossen |
| Bing Search APIs | am 11.08.2025 abgeschaltet (P, Microsoft) | ausgeschlossen |
| Brave Search API | kostenpflichtig (Neuregelung 2026), Speicherrechte gesondert (S) | ausgeschlossen (kostenpflichtig) |
| Marginalia Search API | Public Key stark gedrosselt; kostenloser Schlüssel nur **nicht-kommerziell** (CC BY-NC-SA 4.0); kommerzieller Schlüssel kostenpflichtig (S, Anbieterseite) | ausgeschlossen für Yellotools |
| DuckDuckGo-HTML-Scraping | keine offizielle Such-API; Nutzungsbedingungen verlangen autorisierte Nutzung; Scraping laut Dritten untersagt (S) | ausgeschlossen |
| SearXNG (selbst gehostet) | Software frei (AGPL-3.0), fragt aber Google/Bing u. a. per Scraping ab → deren Bedingungen (S) | ausgeschlossen als Kern; nicht implementiert |
| Common Crawl Index | frei; Index nach URL/Domain, keine Stichwortsuche; Bedingungen untersagen u. a. Weiterverkauf (S) | möglicher späterer Baustein für Domain-Seitenlisten (nicht im Prototyp) |

**Entscheidung (ADR-13):** Die Internetrecherche kombiniert strukturierte freie Quellen:
Firmen finden über **GLEIF-Namenssuche**, **Wikidata** (Suche + Branchen-SPARQL) und
**TED-Ausschreibungen**; Belege über **Unternehmenswebseiten** bekannter Domains; Signale über
**GDELT** und TED. Die Grenze wird offen benannt: Firmen ohne LEI, ohne Wikidata-Eintrag und
ohne öffentliche Ausschreibung findet der Prototyp nicht automatisch. Ergänzung ohne Kosten:
Import vorhandener Listen (Messe-Ausstellerlisten, Verbandsmitglieder, CRM-Export – sofern deren
Nutzung erlaubt ist) als CSV in M9.

## 4. Gesperrt bzw. ausgeschlossen

| Quelle | Grund | Status im Register |
|---|---|---|
| Handelsregister (handelsregister.de) | keine offizielle API; max. 60 Abrufe/h; automatisierte Massenabfragen laut Portal-FAQ (zitiert in Sekundärquellen) unzulässig | `blocked` |
| Unternehmensregister/Bundesanzeiger automatisiert | Bedingungen für automatisierten Abruf ungeklärt | nicht im Register; nur manueller PDF-Upload (M8) |
| BA Jobsuche (`rest.arbeitsagentur.de`) | keine offizielle API (nur Community-Doku); Nutzung ungeklärt | `pending_review`, aus |
| LinkedIn, Xing, Indeed, StepStone, Glassdoor, kununu | AGB, Login, Personenbezug | nicht im Register |
| North Data, OpenCorporates (kommerziell), D&B, Orbis | kostenpflichtig | North Data als `excluded_paid` (nur Transparenz, kein Adapter) |

Ein optionaler Adapter für einen kostenpflichtigen Anbieter darf später entstehen, bleibt aber
standardmäßig deaktiviert und ist nie Voraussetzung (Regel in `ARCHITECTURE.md` §2.2).

## 5. Anwendungsfälle außerhalb der Werbetechnik (Hypothesen)

Definiert in `src/market_intel/data/use_cases.toml` mit Suchbegriffen je Quelle, alle mit
Status `hypothesis` (vom Vertrieb zu bestätigen): Fahrzeugfolierung/Car Wrapping,
Glasfolien (Sonnen-/Sicht-/Sicherheitsfolie), Lackschutzfolie/Fahrzeugaufbereitung,
Werbetechnik/Beschriftung, Möbel-/Innenausbaufolien. Weitere Kandidaten aus Revision 1
(Schienenfahrzeuge, Boots-/Luftfahrt, Druck, Schutzfolien in der Fertigung, Lackierereien,
Etiketten) werden bei Bedarf in derselben Datei ergänzt. CPV-Codes werden bewusst nicht
eingetragen, bis sie fachlich geprüft sind; TED wird per Volltext abgefragt.

## 6. Herkunftsklassen je Quelle

| Quelle | `provenance_class` |
|---|---|
| GLEIF, SEC EDGAR, TED, EZB | `official` |
| Unternehmenswebseite | `company_reported` |
| Wikidata, GDELT | `third_party` |
| EUR-Umrechnung, Wachstumsraten | `derived` (mit Formel und Eingangswerten) |
| Schätzungen | `estimate` – im Prototyp werden **keine** Schätzungen erzeugt |

## 7. Quellenverzeichnis der Recherche (Abruf 2026-10-18)

- GLEIF: [LEI Data Terms of Use](https://www.gleif.org/en/meta/lei-data-terms-of-use), [Level 2 Format](https://www.gleif.org/en/about-lei/common-data-file-format/relationship-record-cdf-format), [LEIScan Terms (CC0-Hinweis)](https://leiscan.com/terms), [INSEE LEI-France CGU](https://lei-france.insee.fr/cgu)
- Wikimedia: [API Usage Guidelines](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_API_Usage_Guidelines), [Access policy](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy), [WDQS-Limits (R-Doku)](https://wikimedia.r-universe.dev/WikidataQueryServiceR/doc/manual.html), [Graph Split](https://meta.wikimedia.org/wiki/WikiCite/WDQS_graph_split)
- SEC: [Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data)
- Companies House: [Rate limiting](https://developer-specs.company-information.service.gov.uk/guides/rateLimiting), [Authorisation](https://developer-specs.company-information.service.gov.uk/guides/authorisation)
- EZB: [Airnode ECB (Reuse-Hinweis)](https://airnodehub.api3.org/explore/ecb), [ecbdata](https://github.com/LucaMingarelli/ecbdata)
- TED: [Developers corner for reusers](https://ted.europa.eu/en/simap/developers-corner-for-reusers), [noble-ronin/ted-tenders-api](https://github.com/noble-ronin/ted-tenders-api), [DEV: TED API ohne Konto](https://dev.to/prime619/free-eu-tender-alerts-from-the-ted-api-one-python-file-no-account-3bph)
- GDELT: [Blog: API Rate Limiting](https://blog.gdeltproject.org/?p=15416), [jwatte: GDELT open data](https://jwatte.com/blog/gdelt-open-data-kalev-leetaru.md)
- Destatis: [Open Data GENESIS-Online API](https://www.destatis.de/EN/Service/OpenData/api-webservice.html), [restatis (Token)](https://correlaid.r-universe.dev/restatis/doc/manual.html)
- Eurostat: [NACE-Umstellung](https://ec.europa.eu/eurostat/web/nace/transition)
- EU-High-Value-Datasets: [Access guide](https://digital-strategy.ec.europa.eu/en/factpages/open-data-and-high-value-datasets-step-step-access-guide), [KvK Open Dataset](https://www.kvk.nl/en/ordering-products/kvk-business-register-open-data-set/)
- Suchdienste: [Microsoft Bing Retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement), [Brave zur Google-Abschaltung](https://brave.com/learn/google-api-shutdown/), [Marginalia API](https://about.marginalia-search.com/article/api), [DuckDuckGo Acceptable Use](https://duckduckgo.com/acceptable-use), [SearXNG-Alternativen (Upstream-AGB-Risiko)](https://you.com/resources/searxng-alternatives), [Common Crawl FAQ](https://commoncrawl.atlassian.net/wiki/spaces/CRWL/pages/2850820/Frequently+Asked+Questions)
- Handelsregister/BA: [bundesAPI/handelsregister](https://github.com/bundesAPI/handelsregister), [bundesAPI/jobsuche-api](https://github.com/bundesAPI/jobsuche-api)
- Urheberrecht: [§ 44b UrhG](https://www.gesetze-im-internet.de/urhg/__44b.html), [Lausen: TDM-Vorbehalt technisch](https://www.lausen.com/blog/der-text-und-data-mining-vorbehalt-technische-umsetzung-der-maschinenlesbarkeit/)
- Yellotools (Sortiment, Suchergebnisse): [Rakel](https://www.yellotools.com/shop/en/squeegees/), [Schneiden](https://www.yellotools.com/shop/en/cutting/knives-cutters-scissors/), [Startseite](https://www.yellotools.com/en/)
