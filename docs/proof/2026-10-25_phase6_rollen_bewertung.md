# Phase 6 – Rollenklassifikation und belastbare Lead-Bewertung: Prüfprotokoll

Datum: 2026-10-25 · Ausgangsstand: Commit `6621350` · Modell: `v3-2026-10-25` · Rollenregeln:
`roles-2026-10-25` · Produktmatrix: `2026-10-25`

Alle Zahlen unten stammen aus tatsächlich ausgeführten Läufen. Die sechs Unternehmen wurden
**nicht neu abgerufen**, sondern auf einer Kopie der Live-Datenbank vom 2026-10-23
(`live-out/live_opportunity.db`, nicht eingecheckt) mit altem und neuem Code bewertet. Neu
abgerufen wurde nur yellotools.com (Abschnitt 5).

## 1. Ausgangsfehler

Im Live-Test (Phase 5) erhielten **Avery Dennison (Fit 81)** und **ORAFOL (Fit 53)** hohe
Produkt-Fit-Werte, obwohl die Treffer Produktnamen von Folienherstellern waren:

| Firma | Treffer (Phase 5) | Art |
|---|---|---|
| ORAFOL | „Banners“, „Vehicle wrapping“, „Labels & stickers“, „Polycarbonate films“ | Menüpunkte |
| Avery | „Paint Protection Films“, „Vehicle Wrapping Films“, „Window Films“ | Menüpunkte |
| Avery | „Discover a dependable, 7-year clear PPF engineered for daily commuters …“ | Produktsatz |
| Avery | „…today premiered an industry-leading training and certification program for Architectural Window Film installation.“ | Schulung für fremde Verarbeiter |
| Umdasch | „Store Design“, „Ladenbau“ (Menü) zusätzlich zu echten Sätzen | Menüpunkte zählten als weitere Begriffe |

## 2. Neue Regeln (Code: `opportunity/roles.py`, Regeln: `data/roles.toml`)

**Belegklassen je Treffer** – nur die erste zählt als direkter Beleg:

| Klasse | Bedingung | Beispiel (echt) |
|---|---|---|
| Tätigkeitssatz | ganzer Satz (≥ 6 Wörter, Satzzeichen am Ende), kein Produkt-/Zielgruppensatz, Selbstbezug (wir/unser/„das Unternehmen“/Firmenname) oder Leistungsangabe am Satzanfang | „An rund 18 Standorten … sorgen die Store Makers von umdasch für außergewöhnliche Projekte im Ladenbau, …“ |
| Produktsatz | Satz mit Eignungs-/Sortimentsformulierung (engineered, designed for, geeignet, Sortiment, launch …) | Avery: „…PPF engineered for daily commuters…“ |
| Zielgruppensatz | nennt Verarbeiter/Installateure oder Schulung/Zertifizierung/Netzwerk | Avery: „Our elite network of … Certified Wrap Installers…“ |
| Fremdsatz | ganzer Satz ohne Bezug auf das Unternehmen selbst | Avery-Newsletter: „A new contest, window film show & more!“ |
| Fragment | Menüpunkt, Kategorie, Produktname, Jobtitel, Überschrift | „Banners“, „Projektleiter / Projektmanager (m/w/d) Ladenbau“ |

**Rollen** – jede Rolle mit eigenem Satzbeleg, mehrere Rollen möglich:

| Rolle | Muster (Auszug) | Ausschlüsse (Auszug) |
|---|---|---|
| Verarbeiter/Installationsbetrieb | Montage, montieren, Installation, installieren, verlegen, folieren/-folierung, bekleben, beschriften, applizieren, verarbeiten, „we install/apply/wrap“ | Produkt- und Zielgruppensätze; verneint (auch „wir montieren keine …“) |
| Hersteller | Hersteller, herstellen, produzieren, Produktion, Fertigung, manufacture, production | „Herstellerangaben“, „namhafter Hersteller“, „von … Herstellern“, „waste is produced“ |
| Händler/Distributor | Großhandel, Distributor, wholesale, reseller, „Händler für/von“ | „über den Groß-/Fachhandel“, „Händlernetz“, „Händler finden“ |

Rollenbeleg verlangt zusätzlich Selbstbezug oder Leistungsangabe am Satzanfang – Sätze über
andere Firmen (db.com: „…Claas is … one of the world’s leading manufacturers…“) zählen nicht.
Bewusst nicht als Verarbeitung: „realisieren“, „Umsetzung“, „Aufbereitung“ (zu allgemein).

**Belegstatus je Anwendungsfall und Deckel der Produktrelevanz** (`opportunity.toml`):

| Status | Bedingung | Deckel | Segment |
|---|---|---|---|
| belegt | Tätigkeitssatz + belegte eigene Verarbeitung/Installation | – | Produkt-Fit-Segmente |
| hinweis | Tätigkeitssatz, eigene Verarbeitung nicht belegt | 59 | Manuelle Prüfung |
| widerspruechlich | Tätigkeitssatz + Gegenbeleg | 59 | Manuelle Prüfung |
| unklar | nur Produktnennung/Menü/Zielgruppe/Fremdsatz/Drittquelle | 34 | Manuelle Prüfung |
| kein_beleg | eigene Angaben, aber keine passende Tätigkeit | 0 | Kein belegter Produkt-Fit |
| keine_daten | keine eigenen Angaben (z. B. Website gesperrt) | 0 | **Keine Daten – Eignung unbekannt** |

**Formel und ihre Grenzen.** Die Formel aus Phase 4 bleibt unverändert (Produktrelevanz =
0,45 Tätigkeit + 0,25 Prozess + 0,15 Quellenqualität + 0,15 Status des Anwendungsfalls;
Priorität = 0,65 Relevanz + 0,35 Potenzial). Geändert wurde nur, **was zählt**: Tätigkeit,
Prozess und Quellenqualität nur aus Tätigkeitssätzen; nur Produktnennungen ergeben 0,2
(sichtbar, nicht ausgeschlossen). Die Deckel sind keine neue Gewichtung, sondern
Voraussetzungen, die je einen Punkt unter den Segmentschwellen 35/60 liegen; `mi
opportunity-model` prüft das. Die Gewichte selbst sind weiterhin Startwerte ohne empirische
Kalibrierung – es gibt keine Abschlussdaten, gegen die sie geprüft werden könnten.

**Doppelzählungen.** (1) Wörtlich gleicher Satz auf mehreren Seiten zählt einmal (Phase 5);
(2) ein Prozessbegriff, der im Tätigkeitsbegriff steckt (Vollfolierung, Lackschutz ↔
Lackschutzfolie), zählt nur als Tätigkeit – bisher zählte er doppelt; (3) „Lackschutzfolie (PPF)“
ist ein Name, nicht zwei Belege.

**Weitere Angaben je Bewertung:** Firmenzuordnung (Register automatisch / LEI vom Nutzer, im
Register gefunden / nur Nutzerliste / ohne Kennung), Datenlage (eigene Angaben / Website gesperrt /
Fehler / keine Website), Gründe für manuelle Prüfung, Verifikationsstand der Produktmatrix –
in Bericht, Oberfläche, CLI und Lead-Export (neue Spalten `belegstatus`, `rolle`, `zuordnung`,
`manuelle_pruefung`, `produktmatrix`).

**Aktuelle Bewertung = jüngste Runde.** Bisher blieben nach einer Modelländerung alte
Bewertungen „aktuell“, wenn ein Anwendungsfall jetzt keinen Fit mehr hat (eine Firma hätte mit
altem v2-Fit weiter als Lead gestanden). Jetzt gilt je Firma nur die jüngste Bewertungsrunde;
die Historie bleibt vollständig gespeichert.

## 3. Auswirkung auf die sechs Testunternehmen (gemessen)

| Firma | vorher (v2) | nachher (v3) | Rolle (Satzbeleg) | Begründung |
|---|---|---|---|---|
| Umdasch Group AG | Fit 80 · Prio 80 · hoher Fit | **Fit 58 · Prio 65 · belegt** · Mittlerer Fit, Expansion | Verarbeiter **und** Hersteller | „Unser Team setzt die Betreuung … nach der Installation der digitalen Lösungen … fort.“, „…über Planung, Fertigung und Montage bis hin zu …“; 80 → 58, weil „Store Design“ (Menü) und „Messestand“ (Satz über den eigenen Messeauftritt) nicht mehr als zusätzliche Begriffe zählen |
| Avery Dennison | Fit 81 · Prio 70 · hoher Fit | **Fit 40 · Prio 44 · hinweis** · Manuelle Prüfung | unklar | einziger Tätigkeitssatz: „From architectural design to vehicle wraps and fleets, our graphic solutions promote creativity …“; Installateur-Netz und Schulungen als Hinweis; Zuordnung vom Nutzer gewählt |
| ORAFOL Europe GmbH | Fit 53 · Prio 62 · mittlerer Fit | **Fit 20 · Prio 41 · unklar** · Manuelle Prüfung | Hersteller | „Our production facilities are at the heart of ORAFOL.“; Anwendungsbezug nur aus Menüpunkten; Grund: „Hersteller ohne belegte eigene Verarbeitung – Werkzeugbedarf unbekannt, nicht ausgeschlossen“ |
| Steinel GmbH | Fit 0 · kein Fit | **Fit 0 · kein_beleg** · kein Fit | Hersteller | „Hier finden Sie unsere Abteilung für OEM-Lösungen für kundenspezifische Entwicklung und Fertigung.“; Impressum zählt weiterhin nicht (Phase 5) |
| Deutsche Bank AG | Fit 0 · kein Fit | **Fit 0 · kein_beleg** · kein Fit | unklar | Claas-Satz ist kein Selbstbezug |
| Sixt SE | Fit 0 · **kein Fit** | **Fit 0 · keine_daten** · **Keine Daten – Eignung unbekannt** | unklar | sixt.de verweigerte den Abruf (403, respektiert); Aussage jetzt „nicht belegt, aber auch nicht ausgeschlossen“ |

Erwartungen aus dem Auftrag: Steinel kein positiver Fit aus Impressum ✔ · Menüpunkte erhöhen
den Score nicht ✔ (Umdasch-Test mit/ohne Menüpunkte: gleicher Wert) · Avery/ORAFOL nicht als
Verarbeiter ✔, aber nicht ausgeschlossen (Segment „Manuelle Prüfung“, Lead-Export) ✔ · Sixt
„keine Daten“ statt „kein Bedarf“ ✔.

Auswirkung auf die erfundenen Testfirmen (Phase-4-Tests): Folienprofi wechselt beim besten
Anwendungsfall von Fahrzeugfolierung (bisher durch Doppelzählung vorn) zu Lackschutz (66 vs.
63, beide „belegt“); die Erwartung im Test wurde mit Begründung angepasst.

## 4. Tests und Gegenproben

- Testsuite: **225 Tests – 223 bestanden, 2 übersprungen** (die beiden Live-Tests, nur mit
  `MI_LIVE=1`), **0 fehlgeschlagen**; neu: 32 in `tests/test_phase6_roles.py`
  (Testtexte: `tests/phase6_real_texts.py`, wörtlich aus dem Live-Test). Synthetische Sätze
  (Händler, Verneinung, Widerspruch, Deckel, Doppelzählung) sind im Test als SYNTHETISCH markiert.
- `ruff` (0.13.0) und `mypy --strict`: ohne Befund.
- Live-Tests (`MI_LIVE=1`) wurden in dieser Phase **nicht** erneut ausgeführt (keine neuen
  Abrufe der sechs Firmen nötig; Bewertung lief auf den gespeicherten Live-Daten).

**Gegenproben (tatsächlich ausgeführt):** Jede Korrektur wurde einzeln außer Kraft gesetzt,
der zugehörige Test lief und musste fehlschlagen; die Datei wurde danach wiederhergestellt und
per SHA-256 geprüft.

| # | außer Kraft gesetzt | Ergebnis |
|---|---|---|
| M1 | Belegklassen (jeder Satz = Tätigkeitssatz) | Test schlägt fehl ✔ |
| M2 | Deckel nach Belegstatus | Test schlägt fehl ✔ (beim ersten Lauf **grün** – Testlücke, Test ergänzt) |
| M3 | Zielgruppe/Schulung schließt Verarbeitung nicht aus | Test schlägt fehl ✔ |
| M4 | Rollenbeleg ohne Selbstbezug | Test schlägt fehl ✔ |
| M5 | jedes Zitat gilt als Satz | Test schlägt fehl ✔ |
| M6 | Datenlage immer „eigene Angaben“ | Test schlägt fehl ✔ |
| M7 | Prozessbegriff = Tätigkeitswort zählt doppelt | Test schlägt fehl ✔ |
| M8 | Klammer-Apposition zählt doppelt | Test schlägt fehl ✔ |
| M9 | kein Satzvorrang je Seite | Test schlägt fehl ✔ |
| M10 | Verneinung nach dem Verb ignoriert | Test schlägt fehl ✔ |
| M11 | Modellprüfung Deckel/Schwellen | Test schlägt fehl ✔ |
| M12 | Matrix „verifiziert“ schon bei einer bestätigten Gruppe | Test schlägt fehl ✔ |
| M13 | Nutzer-LEI nicht als Unsicherheit markiert | Test schlägt fehl ✔ |
| M14 | Händler-Ausschluss „über den Groß-/Fachhandel“ | Test schlägt fehl ✔ (beim ersten Lauf **grün** – Testsatz traf kein Händlermuster; dabei Regel-Lücke „über den Großhandel“ gefunden und behoben) |
| M15 | Konsistenzprüfung des Matrix-Abgleichs | Test schlägt fehl ✔ |
| M16 | alte Bewertungsrunden gelten weiter als aktuell | Test schlägt fehl ✔ |

Unverändert und weiter grün: Bestandskunden- und Ausschlusslogik (Phase-4-Tests sowie neuer
Test mit Hersteller auf Bestandskundenliste und verworfener Firma ohne Daten).

## 5. Produktmatrix – Abgleich mit yellotools.com (2026-10-25)

yellotools.com war am 2026-10-25 **erreichbar** (HTTP 200, keine Cloudflare-Challenge;
2026-10-23 noch gesperrt). robots.txt erlaubt Produktseiten (gesperrt nur Konto/Kasse/Widgets),
kein TDM-Vorbehalt. Abruf mit eigenem User-Agent, 3 s Abstand, ohne Umgehung.

Vorgehen: Jede in der Matrix genannte Herstellerseite abgerufen, Beispielprodukte wörtlich
gesucht; nicht gefundene Namen in der offiziellen Sitemap (`/en/sitemap.xml`, Stand
2026-10-24) gesucht und die Produktseite einzeln abgerufen.

Ergebnis: **38 von 44 Beispielprodukten wörtlich auf offiziellen Seiten gefunden.** Nicht
gefunden: AllStar Gold, AllStar Cutter (Quellseite 404, nicht in der Sitemap), YelloFlex mit
Größenangaben (Produkt „YelloFlex“ gefunden, Größen nicht geprüft), YelloFlex Shape (nur
Händlerquelle), AntiStatic Fluid C/E (nur „AntiStatic Fluid C“ belegt), StreetRoller Basic
(Katalog führt „StreetRoller Flexi“). Zwei Quellseiten liefern 404 (AllStar Cutter,
englische BodyGuardKnife-Seite).

Änderungen an der Matrix: 6 offizielle Produktseiten als Quellen ergänzt (PPF Sandwich,
FeltBloc, YelloFlex, YelloLip Cutter, WallRoller Chrissi, YelloBlade 360), je Gruppe
`source_checked_at`, `examples_on_manufacturer_site`, `examples_not_found`. **Nichts gelöscht
oder umbenannt** – „nicht gefunden“ beweist nicht, dass es ein Produkt nicht gibt. Geprüft
wurden nur Namen und Quellen, **nicht** Funktionen, Materialien oder Grenzen. Status bleibt
`researched`; die Anwendung zeigt: „NICHT verifiziert – 0 von 12 Produktgruppen vom
Hersteller/Vertrieb bestätigt; Herstellerseiten-Abgleich 2026-10-25: 38 von 44
Beispielprodukten wörtlich gefunden“.

## 6. Grenzen und offene Punkte (ehrlich)

1. **Regel- statt Sprachanalyse.** Wer handelt, wird nur über Selbstbezug erkannt. Schwacher
   Selbstbezug zählt mit: „…lassen Sie sich von unserem Newsletter inspirieren.“ (umdasch.com)
   gilt als Tätigkeitssatz zu „Ladenbau“ – erhöht den Wert nicht (gleicher Begriff), ist aber
   inhaltlich schwach. Markennamen ohne Firmennamen („Store Makers“) werden nicht als
   Selbstbezug erkannt.
2. **Rollenbeleg ist firmenweit**, nicht je Anwendungsfall: Umdasch belegt Montage, das gilt für
   alle seine Anwendungsfälle.
3. **Synonyme**: nur die Klammerform „X (Y)“ wird zusammengeführt; „PPF“ und „Paint
   Protection Film“ in verschiedenen Sätzen zählen als zwei Begriffe.
4. **Seiten ohne extrahierbare Angabe** werden einer Firma nicht zugeordnet (Zuordnung läuft
   über Extraktionsbefunde). Live trat das nicht auf (alle Seiten enthielten die Navigation);
   in den Tests wird die Live-Zuordnung deshalb ausdrücklich nachgebildet. Nicht geändert.
5. **Älterer Fehler in der CSV-Pflege der Produktmatrix gefunden, nicht behoben**: Listeneinträge
   mit „;“ (z. B. Grenzen „…ausgelegt; Nutzen … nicht belegt“) zerfallen beim CSV-Re-Import in
   zwei Einträge, weil „;“ auch das Listentrennzeichen ist. Liegt außerhalb dieser Phase.
6. Hersteller wie ORAFOL/Avery: Ob ein Werkzeugbedarf besteht (Anwendungstechnik, Musterbau,
   Schulungen, Partner-/Kanalrolle), ist nicht belegt – deshalb manuelle Prüfung statt
   Ausschluss oder hohem Score.
