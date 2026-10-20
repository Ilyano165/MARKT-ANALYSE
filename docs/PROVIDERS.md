# Anbieter und Adapter – was tatsächlich angebunden ist

Stand: 2026-10-20 · market-intel 0.3.0 · Adapterliste im Programm: `mi adapters`, Quellenregister:
`mi sources`.

**Teststatus-Legende**
- **Mock** – mit nachgebildeten Antworten im dokumentierten Format getestet (automatisiert)
- **Live** – gegen den echten Dienst geprüft. **Bisher: bei keinem Anbieter**, weil die
  Entwicklungsumgebung keinen Zugriff auf die Quellen hat (Proxy 403). Der manuelle
  Integrationstest (`docs/MANUAL_INTEGRATION_TEST.md`) steht aus.

Keine der unten genannten APIs ist erfunden; Endpunkte und Feldnamen stammen aus der
Dokumentation der Anbieter bzw. aus dokumentierten Wiederverwender-Beispielen (Prüfstand in
`RESEARCH_SOURCES.md`). Wo ein Format nicht gesichert ist, steht das unten ausdrücklich.

## 1. Übersicht nach den zehn Quellentypen

| # | Quellentyp | Umsetzung | Status |
|---|---|---|---|
| 1 | Allgemeine Websuche über Such-APIs | **nicht angebunden** – keine kostenlose, kommerziell zulässige Such-API verfügbar (Google CSE geschlossen, Bing abgeschaltet, Brave kostenpflichtig, Marginalia nur nicht-kommerziell). Ersatz: strukturierte Suche über Register, Wikidata, TED, Firmenlisten | bewusst offen |
| 2 | Offizielle Unternehmenswebseiten | `WebsiteAdapter` + Extraktion | implementiert, Mock |
| 3 | Geschäftsberichte / IR-Seiten | `WebsiteAdapter` folgt IR-/Berichtslinks zu PDFs derselben Domain; `mi import-document` für bereitgestellte PDFs | implementiert, Mock |
| 4 | Jahresabschlüsse und Register | SEC EDGAR (XBRL), Companies House (Profil); Bundesanzeiger/Unternehmensregister nur per Upload | implementiert, Mock |
| 5 | Nationale/internationale Register | GLEIF, Wikidata, Companies House | implementiert, Mock |
| 6 | Stellenanzeigen / Karriereportale | Karriereseiten der Firmen (schema.org `JobPosting`); externe Jobportale nicht (AGB) | implementiert, Mock |
| 7 | Pressemitteilungen / Wirtschaftsnachrichten | Presse-/News-Seiten der Firmen, GDELT DOC 2.0, TED-Ausschreibungen | implementiert, Mock |
| 8 | Verbände / Messeverzeichnisse | CSV-Import (`mi import-companies`), danach `--list` im Auftrag; kein Scraping von Verzeichnissen | implementiert, Mock |
| 9 | Statistiken / Branchenberichte | Eurostat (JSON-stat 2.0) | implementiert, Mock; **Datensatzcode live ungeprüft** |
| 10 | Lizenzierte Datenanbieter | **nicht angebunden** – Schnittstelle (`BaseAdapter`, Kostenbudget im Gateway) vorhanden, aber kein Vertrag und keine verifizierte API-Dokumentation; standardmäßig wäre jede Quelle mit Preis durch das 0-€-Budget gesperrt | bewusst offen |

## 2. Je Anbieter

### GLEIF LEI API (`gleif`, aktiv)
- API: ja, REST/JSON:API, `https://api.gleif.org/api/v1` – ohne Schlüssel, kostenlos
- Genutzt: `GET /lei-records?filter[fulltext]=…&filter[entity.legalAddress.country]=…`,
  `GET /lei-records/{lei}`, `GET /lei-records/{lei}/direct-parent` bzw. `/ultimate-parent`
- Daten: LEI, rechtlicher Name, andere Namen, Sitz-/Hauptverwaltungsadresse, Registernummer,
  Status, Mutterbeziehungen (Konsolidierung, ohne Beteiligungsquote)
- Funktionen: Kandidatensuche nach Namensbegriffen, Auflösung genannter Firmen (Ähnlichkeit
  ≥ 0,85), Untersuchung per LEI, 404 bei Mutter = „keine Mutter gemeldet“
- Bedingungen: GLEIF LEI Data Terms of Use; Daten laut Wiederverwendern CC0
- Test: Mock (inkl. kaputter/unvollständiger Antworten)

### Wikidata (`wikidata`, aktiv)
- API: ja – Action API (`wbsearchentities`, `wbgetentities`), `Special:EntityData`, SPARQL
- Daten: QID, Website (P856), Land (P17 → P297), Branche (P452), LEI (P1278), SEC-CIK (P5531),
  Mitarbeiter (P1128) und Umsatz (P2139) **nur mit Stichtag (P585)**, Währung über P498
- Funktionen: Kandidaten über Branchen-SPARQL je Land, Zuordnung nur über gleichen LEI
- Phase 3: Hauptsitz (P159) mit Ortskoordinaten (P625 des Ortes → Genauigkeit „city“, Qualifikator
  P625 am Hauptsitz → „site“), Produkte (P1056) und Marken (P1716) als Profilangaben,
  Tochter-/Muttergesellschaften (P355/P749, Beziehung `wikidata_parent`), Tätigkeitsgebiet (P2541)
  als „beliefert“; Beleg ist das JSON-Fragment `"id":"Q…"` im gespeicherten EntityData-Dokument.
  **Eigenschafts-IDs nach Wikidata-Dokumentationsstand, live nicht geprüft** (Wikidata ist aus
  der Entwicklungsumgebung gesperrt). Grenzen: höchstens 20 Produkte/Marken, 25 Töchter,
  15 Gebiete je Firma (Abruflast)
- Bedingungen: CC0; User-Agent mit Kontakt; SPARQL-Rechenzeitlimit
- Herkunftsklasse: `third_party`
- Test: Mock

### SEC EDGAR (`sec_edgar`, aktiv)
- API: ja – `www.sec.gov/files/company_tickers.json`, `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- Daten: Jahresumsätze (10-K/20-F/40-F, Geschäftsjahre 340–380 Tage, spätere Korrektur
  gewinnt), Mitarbeiter nur falls als `dei:EntityNumberOfEmployees` gemeldet
- Zuordnung: CIK nur über starke Kennung (Wikidata P5531); Namenstreffer → Prüfhinweis
- Bedingungen: gemeinfrei; ≤ 10 Anfragen/s; User-Agent mit E-Mail
- Test: Mock

### UK Companies House (`companies_house`, aktiv **nur mit kostenlosem Schlüssel**)
- API: ja – `GET /search/companies`, `GET /company/{nummer}`, Basic Auth (Schlüssel als Benutzer)
- Daten: Name, Registernummer, Status, SIC-Codes, Sitz, Gründungsdatum. **Keine** Umsatz- oder
  Mitarbeiterzahlen (nicht strukturiert in der API)
- Zugang: kostenloses Konto + Schlüssel in `MI_COMPANIES_HOUSE_KEY`
- Test: Mock

### EZB Data Portal (`ecb_fx`, aktiv)
- API: ja – `data-api.ecb.europa.eu/service/data/EXR/A.{CUR}.EUR.SP00.A?format=csvdata`
- Daten: Jahresdurchschnittskurse → abgeleitete EUR-Werte (Herkunft `derived`, Formel gespeichert)
- Test: Mock

### TED Search API v3 (`ted`, aktiv)
- API: ja – `POST api.ted.europa.eu/v3/notices/search`, anonym
- Genutzt: Felder `publication-number`, `notice-title`, `buyer-name`, `buyer-country`,
  `publication-date`, `classification-cpv`, `notice-type`; Abfrage `FT~"…" AND buyer-country=DEU AND PD>=…`
- Daten: Ausschreibungen → öffentliche Auftraggeber + Signale. **Zuschlagsempfänger
  (Gewinner) werden nicht ausgewertet**, weil der Feldname nicht verifiziert ist
- Test: Mock

### GDELT DOC 2.0 (`gdelt`, aktiv)
- API: ja – `api.gdeltproject.org/api/v2/doc/doc?mode=artlist&format=json`
- Daten: Titel, URL, Datum, Domain von Nachrichten (kein Artikeltext)
- Funktionen: Marktsignale je Anwendungsfall; Nachrichten je untersuchter Firma
- Test: Mock (inkl. Nicht-JSON-Antwort)

### Unternehmenswebseiten (`website`, aktiv)
- Kein API – zulässiger Abruf öffentlicher Seiten mit robots.txt, TDM-Vorbehalt (§ 44b UrhG),
  Drosselung, Größenlimit, nur gleiche Domain/Subdomains, keine Formulare, kein Login
- Abgerufen: Startseite + priorisierte Unterseiten (Impressum, Karriere, IR, Presse,
  Unternehmen, Produkte; max. 8) + bis zu 3 Bericht-PDFs (≤ 20 MB)
- Extraktion (deterministisch, `engine/extract.py`): Umsatz mit Währung/Skala/Zeitraum,
  Mitarbeiter, Handelsregisternummer und USt-IdNr. (nur Impressum), Stellenanzeigen (JSON-LD),
  Pressemeldungen (neuer Standort, Erweiterung, Investition, Übernahme, Partnerschaft),
  Anwendungsfall- und Suchprofil-Belege; optional KI-Klassifikation mit Zitatprüfung
- Phase 3 (`extraction/profile.py`): schema.org-`Organization` (Namen, Marken, Adresse und
  Abteilungen/Standorte mit `GeoCoordinates` → Genauigkeit „site“, `areaServed`, Angebotskatalog),
  Länder in Sätzen über Werke/Niederlassungen (physisch, nur Land), Export/Vertrieb (beliefert)
  oder Präsenz (erwähnt), „in über 40 Ländern“, Vertriebswege und Zielgruppen
  (`data/profile_lexicon.toml`), Überschriften auf Produkt-/Leistungsseiten (Vertrauen „low“);
  Pressetexte zusätzlich als Unternehmensereignis (`unverified`)
- Website-Adresse kommt aus Wikidata, Firmenlisten oder (künftig) dem Impressum – **ohne
  Websuche findet das Programm keine Website, die in keiner Quelle steht**
- Test: Mock (inkl. kaputtem HTML, kaputtem JSON-LD, Prompt-Injection-PDF, robots-Sperre)

### Eurostat (`eurostat`, aktiv)
- API: ja – `ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{datensatz}?format=JSON`
- Parser für JSON-stat 2.0 vollständig; gespeichert in `market_statistic`
- **Einschränkung:** Standard-Datensatz `sbs_ovw_act` und Filter `nace_r2=C2573` sind live
  ungeprüft. Scheitert der Abruf, wird das als Hinweis ausgewiesen; der Auftrag läuft weiter
- Wird nur genutzt, wenn die Strategie NACE-Codes enthält (Lexikon, z. B. 25.73)
- Test: Mock

### Bereitgestellte Daten (`user_list`, `user_upload`, lokal)
- `mi import-companies liste.csv --list-name … --source-url …` (Spalten `name`, optional
  `country`, `website`, `lei`, `city`); Zuordnung zu bestehenden Firmen nur über Domain/LEI
- `mi import-document bericht.pdf --company … [--source-url … --published JJJJ-MM-TT]` –
  Original-URL und Datum werden nur gespeichert, wenn angegeben
- Test: Mock

### Gesperrt / nicht angebunden
Handelsregister (automatisiert unzulässig), Bundesagentur-Jobsuche (keine offizielle API),
LinkedIn/Xing/Indeed/StepStone (AGB), Marginalia (nur nicht-kommerziell), North Data u. a.
(kostenpflichtig). Siehe `RESEARCH_SOURCES.md` §3–4.

## 3. KI-Provider

| Provider | Schnittstelle | Status |
|---|---|---|
| keiner (Standard) | – | aktiv; alle Funktionen regelbasiert |
| Ollama | `/api/tags`, `/api/chat` mit JSON-Schema (`format`) | implementiert, Mock |
| OpenAI-kompatibler lokaler Server (llama.cpp-Server, LM Studio, vLLM) | `/v1/models`, `/v1/chat/completions` mit `response_format: json_schema` | implementiert, Mock |
| Cloud-Anbieter | – | bewusst nicht implementiert (Kosten, Datenabfluss) |

KI-Aufgaben (`llm/assistant.py`): Suchbegriffe vorschlagen (Firmennamen nur, wenn wörtlich im
Auftrag), relevante Passagen und Anwendungsfälle (nur mit geprüftem Zitat), Profil-
zusammenfassung (nur wenn jeder Satz [F…]-Referenzen trägt und jede Zahl belegt ist), mögliche
Bedarfe (als „KI-Hypothese, nicht belegt“), Widerspruchshinweise (als „ungeprüft“). Zahlen,
Zeitreihen, Dubletten und Validierung macht immer der Programmcode.

## 4. Neuen Anbieter hinzufügen

1. Quelle in `src/market_intel/sources/registry.py` eintragen (Lizenz, Grenzen, Kosten,
   `legal_status`, `cost_per_call_eur`).
2. Abruf-/Parserfunktionen in `src/market_intel/sources/<name>.py` – nur über
   `ctx.gateway`, Antworten mit `sources/common.json_object` robust auswerten.
3. Adapterklasse von `BaseAdapter` ableiten (`find_candidates` und/oder `investigate`),
   `kinds` setzen, in `ADAPTERS` eintragen.
4. Mock-Tests inkl. fehlerhafter Antworten ergänzen; Eintrag in diesem Dokument.
Pipeline, Budget, Fortschritt, Bericht und Export funktionieren ohne weitere Änderung.
