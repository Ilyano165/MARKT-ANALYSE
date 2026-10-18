# Recherchequellen – Yellotools Market Intelligence

Stand: 2026-10-18 · Phase 1

Dieses Dokument bewertet Datenquellen nach Nutzen, Zugang, Lizenz/Nutzungsbedingungen und
Kosten. **Keine Quelle wird im Code aktiviert, bevor ihr `legal_status` im Quellenregister auf
`approved` steht** (siehe `DATA_MODEL.md` §3.17, `SECURITY_AND_PRIVACY.md` §4).

Spalte **Prüfstand** zeigt, wie belastbar die Angaben in dieser Tabelle sind:

- **P** – auf Primärquelle (offizielle Seite/Dokumentation des Anbieters) gestützt
- **S** – nur über Sekundärquellen recherchiert; vor Aktivierung gegen die Primärquelle prüfen
- **O** – offen, nicht recherchiert

Die Recherche erfolgte per Websuche; die Seite `yellotools.com` war aus der
Entwicklungsumgebung nicht direkt abrufbar (Netzwerkrichtlinie), Angaben dazu stammen aus
Suchergebnissen.

---

## 1. Ausgangslage Yellotools (für Customer Discovery)

Öffentlich belegt (Suchergebnisse von yellotools.com): Hersteller von Spezialwerkzeugen für
Werbetechnik/Beschriftung und Car Wrapping, Entwicklung und Fertigung in Windeck (Deutschland),
nach eigener Angabe seit 2004. Sortimentsbereiche im Onlineshop u. a.: Schneiden (Cutter,
Messer, Scheren, Kreisschneider, Klingen), Rakel (Kunststoff-, Magnet-, Holzrakel; laut
Shopbeschreibung für Folien auf Fahrzeugen, Glas, Fassaden und Böden), Entfernen (Folien- und
Kleberückstände), Messen/Markieren (Lineale, Schneidebänder), Car-Wrapping-Hilfsmittel
(Applikationshilfen, Heißluft/Brenner), Handschuhe, Spender, Funktionsmöbel.

**Die Produkttaxonomie im System wird nicht aus dieser Liste gebaut**, sondern aus dem
Yellotools-Artikelstamm (Export, Format wie im Auftragsimport-Katalog) und vom Vertrieb
freigegeben (`product_category.status = approved`).

### Anwendungsfälle außerhalb der Werbetechnik (Hypothesen)

Alle Einträge haben `evidence_status = hypothesis` und müssen vom Yellotools-Vertrieb bestätigt
oder verworfen werden. Sie dienen nur als Startpunkt für Suchbegriffe.

| Anwendungsfall | mögliche Zielbranchen (NACE-Abschnitt/-Abteilung als Startwert) | Produktbezug |
|---|---|---|
| Fenster-/Sonnenschutz-/Sicherheitsfolien auf Glas | Glaserei, Fensterbau (C 23.1, F 43.34), Gebäudetechnik | Rakel, Cutter, Entfernen |
| Lackschutzfolie (PPF), Fahrzeugaufbereitung | Kfz-Handel/-Aufbereitung (G 45), Karosseriebau | Rakel, Cutter, Heißluft |
| Fahrzeugbeschriftung für Flotten | Fuhrparks von Logistik, Handwerk, ÖPNV (H 49) | gesamtes Sortiment |
| Schienenfahrzeuge (Folierung, Graffitischutz) | Schienenfahrzeugbau/-instandhaltung (C 30.2) | Entfernen, Rakel |
| Boots-/Yachtbau, Luftfahrt-Instandhaltung (Dekorfolien) | C 30.1, C 30.3, MRO (C 33.16) | Rakel, Cutter |
| Möbel- und Innenausbaufolien (Architekturfolien) | Innenausbau (F 43.3), Ladenbau, Messebau (N 82.3) | Rakel, Cutter, Heißluft |
| Bodenmarkierung/-folien | Industrieböden, Logistikflächen | Rakel, Schneidwerkzeuge |
| Großformatdruck, Digitaldruck | Druckereien (C 18.1) | Schneiden, Messen |
| Schutzfolien in der Fertigung (Entfernen, Zuschnitt) | Metall-/Kunststoffverarbeitung (C 22, C 25), Elektronik (C 26) | Cutter, Entfernen |
| Abklebearbeiten in Lackierereien | Lackierbetriebe (F 43.34, C 25.61) | Schneidebänder, Cutter |
| Etiketten/Verpackung (Liner schneiden) | C 17.2, C 18.1 | Cutter |

## 2. Quellenübersicht

### 2.1 Register, Identifikatoren, Konzernstrukturen

| Quelle | Nutzen | Zugang | Lizenz / Bedingungen | Kosten | Prüfstand | Empfehlung |
|---|---|---|---|---|---|---|
| **GLEIF LEI API** (`api.gleif.org`) | LEI, rechtlicher Name, Sitz, Registernummer, Mutter (direkt/ultimativ, Level 2) | REST, ohne Authentifizierung | laut Sekundärquellen CC0 (Golden Copy); Level 2 = Konsolidierung, keine Prozente, Lücken durch Ausnahmemeldungen | frei | S | **Phase A**, nach Lizenzprüfung |
| **Wikidata** (SPARQL/REST) | QID, Website, Branche, Sitz, Mutterfirma, ggf. Mitarbeiter/Umsatz | WDQS; Limits laut Handbuch: 60 s Abfragezeit je 60 s je Client (User-Agent+IP), 60 s Timeout | Daten CC0 (zu bestätigen); Angaben **`third_party`**, oft ohne Stichtag | frei | S | **Phase A** für IDs und Struktur; Kennzahlen nur mit Quellenangabe in Wikidata übernehmen |
| **Handelsregister** (handelsregister.de) | Rechtsform, Registernummer, Sitz, Gesellschafter-/Vertretungsdaten | nur Webformular, keine offizielle API | laut Sekundärquellen max. 60 Abrufe/h; automatisierte Massenabfragen werden vom Portal als möglicher Fall von §§ 303a/b StGB bezeichnet; IP-Freischaltung über Servicestelle Registerportal (AG Hagen) möglich | Registerabrufe teils gebührenpflichtig | S | **Kein automatisierter Abruf.** Nur nutzerausgelöster Einzelabruf als Link/Upload oder über lizenzierten Anbieter |
| **Unternehmensregister / Bundesanzeiger** | Jahresabschlüsse (Umsatz nur bei großen/mittelgroßen Gesellschaften offengelegt, Bilanz ab Kleinstgesellschaften eingeschränkt) | Webportal | Bedingungen für automatisierten Abruf nicht geklärt; Sekundärquelle nennt Gebühr je automatisiertem Abruf (JVKostG) | unklar | S/O | Rechtsklärung; bis dahin nur manueller Upload eines Abschluss-PDFs durch den Nutzer → Extraktion |
| **North Data** Data API | DE/EU-Registerdaten, Bilanzkennzahlen, Beteiligungen, Historie | REST mit API-Schlüssel | AGB nur für Unternehmer; Vertrag | laut Preisseite: Paket „Firmendaten Basis“ 500 €/Monat inkl. 5 000 Abrufe, danach 0,10–0,05 €/Abruf | P | **stärkster Kandidat für DE-KMU-Kennzahlen**; Testzugang und Vertrag prüfen (Speicherrechte!) |
| **OpenCorporates** API | weltweite Registerdaten | REST | frei nur „share-alike“ (ODbL, Rückgabe unter offener Lizenz); kommerziell kostenpflichtig | ab £2 250/Jahr (Essentials) laut Preisseite | P | optional, wenn internationale Registerabdeckung fehlt |
| **SEC EDGAR** (`data.sec.gov`, companyfacts/XBRL) | Umsatz, Mitarbeiter (10-K), Segmente US-börsennotierter Firmen | REST/JSON, ohne Schlüssel | max. 10 Anfragen/s, User-Agent mit Firma und Kontakt Pflicht, keine „unklassifizierten“ Bots | frei | P | **Phase B** für US-Marktführer |
| **UK Companies House** API | Firmenstammdaten, Konzern, Abschlüsse (Dokumente) | REST mit API-Schlüssel | 600 Anfragen je 5 Minuten je Anwendung; Bulk- und Streaming-Produkte für Massendaten | frei | P | **Phase B** (UK) |
| **KvK Open Dataset** (NL) | Basis-Firmendaten (Rechtsform, Status, Tätigkeitscodes) | Bulk + API | CC BY 4.0 | frei | P | Phase C |
| **CRO Open Data** (IE) | Firmenstammdaten, tägliche Momentaufnahme | Bulk + API | CC BY 4.0; EU-High-Value-Dataset | frei | P | Phase C |
| **Weitere EU-Register (High-Value Datasets, VO (EU) 2023/138)** | Stammdaten je Land | je Land unterschiedlich | meist CC BY 4.0 | meist frei | S | je Zielmarkt einzeln prüfen; BRIS/e-Justice bietet laut Quellen keine Massen-API |

### 2.2 Amtliche Statistik (Marktgröße, Branchenstruktur)

| Quelle | Nutzen | Zugang | Lizenz | Prüfstand | Empfehlung |
|---|---|---|---|---|---|
| **Destatis GENESIS-Online** | Unternehmen, Beschäftigte, Umsatz je WZ-Klasse, Region | REST/JSON-Webservice | Datenlizenz Deutschland – Namensnennung 2.0; Nutzung laut Destatis ohne Registrierung (für API ggf. Zugangsdaten) | P | **Phase B** |
| **Eurostat** (SBS, Außenhandel) | Strukturdaten je NACE und Land, EU-weit | REST (SDMX/JSON) | Eurostat-Wiederverwendungsregeln (Quellenangabe) | S | **Phase B**; Achtung: ab Berichtsjahr 2025 NACE Rev. 2.1 → Zeitreihenbruch, Rückrechnungen nur teilweise |
| **EZB Data Portal** | Referenzwechselkurse für Währungsumrechnung | REST | frei mit Quellenangabe | O | **Phase A** (für abgeleitete Werte) |
| Statistikämter anderer Zielländer | Marktdaten je Land | unterschiedlich | unterschiedlich | O | bei Markterweiterung |

### 2.3 Unternehmenseigene Quellen

| Quelle | Nutzen | Regeln | Empfehlung |
|---|---|---|---|
| **Unternehmenswebseite** (Impressum, „Über uns“, Standorte, Presse, Karriere, Investor Relations) | Standorte, Selbstangaben zu Mitarbeitern/Märkten, Anwendungsfall-Belege, Geschäftsberichte als PDF | robots.txt verbindlich, TDM-Vorbehalt beachten, Tiefe ≤ 3, max. 50 Seiten je Domain und Run, 1 Anfrage/2 s je Host, keine Formulare, kein Login | **Phase B** |
| **Geschäftsberichte, Nachhaltigkeitsberichte (PDF)** | Umsatz, Mitarbeiter, Segmente, Länder, Standorte, Investitionen | Herkunftsklasse `company_reported` (geprüfte Abschlüsse in Pflichtveröffentlichung: `official`) | **Phase B** (Extraktion M8) |
| **Pressemitteilungen** | Investitionen, neue Standorte, Übernahmen | eigene Kurzfassung, Zitat ≤ 1 000 Zeichen | Phase C |

### 2.4 Stellenmarkt

| Quelle | Nutzen | Zugang / Bedingungen | Prüfstand | Empfehlung |
|---|---|---|---|---|
| **Karriereseiten der Unternehmen** | offene Stellen, Standorte, Tätigkeiten (Indiz für Wachstum und Anwendungsfälle) | wie Webseiten | – | **Phase C (Standard)** |
| **BA Jobsuche** (`rest.arbeitsagentur.de`) | größte DE-Stellendatenbank | laut Community-Projekt *bundesAPI* **keine offizielle API**; Endpunkt mit öffentlich bekanntem Client-Schlüssel; Nutzungsbedingungen für automatisierten Zugriff ungeklärt | S | **gesperrt (`pending_review`)**, bis die BA die Nutzung bestätigt; dann nur aggregierte Zählungen |
| LinkedIn, Xing, Indeed, StepStone, Glassdoor, kununu | – | AGB untersagen Scraping, Login/Personenbezug | – | **ausgeschlossen**; nur über offizielle, vertraglich lizenzierte Schnittstellen, falls je angeboten |

### 2.5 Ausschreibungen und Signale

| Quelle | Nutzen | Zugang | Lizenz | Prüfstand | Empfehlung |
|---|---|---|---|---|---|
| **TED** (Tenders Electronic Daily, EU) | Ausschreibungen/Zuschläge nach CPV-Code und Land → Bedarf (z. B. Fahrzeugfolierung für Flotten, Glasfolien öffentlicher Gebäude), Gewinner als potenzielle Kunden | Search API ohne Authentifizierung, Bulk-XML | Wiederverwendung laut Sekundärquellen nach Beschluss 2011/833/EU mit Quellenangabe | P (API), S (Lizenz) | **Phase C** |
| Nationale Vergabeportale | ergänzend | unterschiedlich | unterschiedlich | O | später |
| Pressedienste/Nachrichten | Expansion, Insolvenzen, Entlassungen | meist kostenpflichtig/AGB-gebunden | – | O | nur lizenziert oder über Such-API mit Link auf Original |

### 2.6 Suchmaschinen (Discovery)

| Dienst | Stand | Bedingungen | Prüfstand | Empfehlung |
|---|---|---|---|---|
| **Brave Search API** | aktiv | laut Sekundärquellen 2026 nutzungsbasiert ca. 5 USD je 1 000 Anfragen; **Speichern von Ergebnissen erfordert eigene Speicherrechte** | S | **Standard-Such-Adapter**; gespeichert werden nur URL, Titel und Zeitpunkt als Lead – Fakten stammen immer aus dem anschließend abgerufenen Originaldokument; Vertragsbedingungen vor Start prüfen |
| **Anthropic Web Search** (Server-Tool der Claude API) | aktiv | Abrechnung über Anthropic-Vertrag | S | Alternative zum Brave-Adapter hinter demselben `SearchPort`; nur für URL-Kandidaten, nie als Faktenquelle |
| Google Custom Search JSON API | für Neukunden geschlossen, Abschaltung zum 01.01.2027 | – | S | **ausgeschlossen** |
| Bing Search APIs | am 11.08.2025 abgeschaltet | – | P | **ausgeschlossen** |

### 2.7 Branchenverbände und Messen

Ausstellerlisten und Mitgliederverzeichnisse (z. B. Messen der Werbetechnik/Großformatdruck wie
FESPA, Fachverbände der Zielbranchen) sind wertvolle Kandidatenlisten. Sie sind häufig
urheber- bzw. datenbankrechtlich geschützt (§ 87b UrhG). **Nur nach Prüfung der
Nutzungsbedingungen je Verzeichnis**, bevorzugt manueller Import einer vom Verband
bereitgestellten Liste.

### 2.8 Kommerzielle Wirtschaftsdatenbanken (Bewertung offen)

North Data (siehe 2.1), Dun & Bradstreet, Bureau van Dijk (Orbis), Creditreform/Firmenwissen,
Implisense, Echobot u. a. Bewertung in M7 anhand von: Abdeckung DE-KMU, Bilanzkennzahlen-
Historie, Speicher- und Weiterverarbeitungsrechte, Preis je Abruf, API-Qualität. Entscheidung
trifft Yellotools (Budget). Ohne Lizenz wird **keine** dieser Datenbanken abgefragt oder
gescrapt.

## 3. Quellenklassen und Herkunftsklasse

| Quelle | Standard-`provenance_class` |
|---|---|
| Register, Bundesanzeiger-Pflichtveröffentlichung, SEC-Filing, Statistikamt | `official` |
| Unternehmenswebseite, freiwilliger Geschäftsbericht, Pressemitteilung | `company_reported` |
| Wikidata, North Data (aufbereitet), Presse, Verband | `third_party` (North Data mit Verweis auf Registerquelle kann auf `official` gehoben werden, wenn das Originaldokument referenziert ist) |
| Eigene Berechnung (Wachstum, Umrechnung, Konzernsumme) | `derived` |
| Schätzmodell (z. B. Mitarbeiter aus Größenklasse) | `estimate` |

## 4. Abrufstrategie je Run-Typ

1. **Company Profile**: Identifikatoren (GLEIF, Wikidata, Register-ID aus Webseiten-Impressum)
   → Konzernstruktur (GLEIF L2) → strukturierte Kennzahlen (SEC/Companies House/North Data, je
   Land) → Webseite (Standorte, Selbstangaben, Berichte) → Extraktion → Lücken dokumentieren.
2. **Market Leaders** (Branche × Land): Kandidaten aus Statistik-Klassifikation + Datenanbieter
   + Suche → Entity Resolution → Profile wie 1 (begrenzt auf Top-N nach Budget) → Ranking nach
   belegtem Umsatz/Mitarbeitern, Unbekanntes bleibt sichtbar unbekannt.
3. **Customer Discovery** (Produktkategorie × Land): Anwendungsfälle → Suchbegriffe und
   NACE-Codes → Kandidaten (Datenanbieter, TED-Gewinner, Suche) → Belege für Anwendungsfall
   (Webseite, Karriere, Ausschreibungen) → Produktfit → Scoring.
4. **Watchlist Refresh**: je Firma nur Quellen mit möglicher Änderung (Cache-Validierung,
   neue Geschäftsjahre, neue Signale) → Änderungsereignisse.

## 5. Adapter-Vertrag (für jede Quelle)

Jeder Adapter muss vor Aktivierung liefern:

- Eintrag im Quellenregister mit Lizenz, Bedingungen, Limits, Kosten, Aufbewahrungsregel
- aufgezeichnete Antworten (Fixtures) für Vertragstests, inkl. Fehlerfällen (404, 429, 5xx,
  leere Antwort, Formatänderung)
- Abbildung jedes gelieferten Feldes auf eine Beobachtung mit Einheit, Zeitraum und
  Herkunftsklasse – oder begründetes Weglassen
- optionaler Live-Smoke-Test (nur manuell/nächtlich, nie im normalen CI)

## 6. Quellenverzeichnis der Recherche (Abruf 2026-10-18)

- Yellotools Shop (Suchergebnisse): [Cutter-Set](https://www.yellotools.com/shop/en/cutting/knives-cutters-scissors/knife-set-cutting-tool-set-for-signmakers-and-film-applicators), [Rakel](https://www.yellotools.com/shop/en/squeegees/), [Entfernen](https://www.yellotools.com/shop/en/removal/), [Startseite](https://www.yellotools.com/en/), [Car Wrapping](https://www.yellotools.com/en/Tools/Car-Wrapping/?p=5)
- Handelsregister (sekundär): [bundesAPI/handelsregister](https://github.com/bundesAPI/handelsregister), [companydata.com Handelsregister-Guide](https://companydata.com/de/handelsregister/)
- Bundesanzeiger/Unternehmensregister (sekundär): [Ecovis Register-Übersicht](https://ecovis-kso.com/blog/register-uebersicht-das-unternehmensregister/), [rulecalc JVKostG](https://rulecalc.com/de/steuern/jutizverwaltungskosten-register-rechner)
- BA Jobsuche: [bundesAPI/jobsuche-api](https://github.com/bundesAPI/jobsuche-api)
- GLEIF: [GLEIF Level 2 Format](https://www.gleif.org/en/about-lei/common-data-file-format/relationship-record-cdf-format), [GDELT GLEIF Level 2](https://docs.gdeltcloud.com/api-reference/reference-data/gleif-level-2-relationships)
- Destatis: [Open Data GENESIS-Online API](https://destatis.de/EN/Service/OpenData/api-webservice.html)
- Eurostat NACE Rev. 2.1: [NACE transition](https://ec.europa.eu/eurostat/web/nace/transition), [NACE Rev. 2.1 official](https://ec.europa.eu/eurostat/web/products-eurostat-news/w/wdn-20230210-1)
- North Data: [Datenprodukte/Preise](https://northdata.de/_data), [API-Doku](https://github.com/northdata/api/), [AGB](https://northdata.de/_agb)
- OpenCorporates: [Pricing](https://opencorporates.com/pricing/), [Terms of use](https://opencorporates.com/terms-of-use-2/)
- SEC: [Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data)
- Companies House: [Rate limiting](https://developer-specs.company-information.service.gov.uk/guides/rateLimiting)
- EU High-Value Datasets: [Access guide](https://digital-strategy.ec.europa.eu/en/factpages/open-data-and-high-value-datasets-step-step-access-guide), [KvK Open Dataset](https://www.kvk.nl/en/ordering-products/kvk-business-register-open-data-set/), [CRO Company Records](https://data.gov.ie/en_GB/dataset/companies/resource/3fef41bc-b8f4-4b10-8434-ce51c29b1bba)
- TED: [Developers corner](https://ted.europa.eu/en/simap/developers-corner-for-reusers), [Search API](https://docs.ted.europa.eu/api/2.0/search.html)
- Wikidata-Limits: [WikidataQueryServiceR-Doku](https://wikimedia.r-universe.dev/WikidataQueryServiceR/doc/manual.html)
- Suchdienste: [Brave Search API Pricing (costbench)](https://costbench.com/software/ai-search-apis/brave-search-api/), [Brave zu Google-Abschaltung](https://brave.com/learn/google-api-shutdown/), [Microsoft: Bing Search API Retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)
- Urheberrecht TDM: [§ 44b UrhG](https://www.gesetze-im-internet.de/urhg/__44b.html), [Lausen: TDM-Vorbehalt technisch](https://www.lausen.com/blog/der-text-und-data-mining-vorbehalt-technische-umsetzung-der-maschinenlesbarkeit/), [activeMind: LAION-Urteil](https://www.activemind.legal/de/guides/urteil-data-mining)
