# Yellotools-Produkt- und Anwendungsmatrix

Stand: 2026-10-25 · Dateien: `src/market_intel/data/products.toml` (12 Produktgruppen),
`src/market_intel/data/use_cases.toml` (21 Anwendungsfälle)

## 1. Wie die Matrix entstanden ist (ehrlich)

- Grundlage sind **öffentliche Yellotools-Produktseiten und -Kategorien**, ermittelt am
  2026-10-20 über eine Websuche. Die Seiten selbst waren aus der Entwicklungsumgebung **nicht
  direkt abrufbar** (Netzwerkrichtlinie). Inhalte stammen daher aus Suchergebnis-Auszügen der
  Herstellerseiten und, wo vermerkt, von Händlerseiten (z. B. YelloFlex-Angaben).
- Jede Produktgruppe trägt den Status **`researched`**: Funktionen und Grenzen sind aus
  Herstellerangaben zusammengefasst, **vom Yellotools-Vertrieb/Produktmanagement nicht
  bestätigt**. Erst nach Prüfung auf `confirmed` setzen (mit `confirmed_by`, `confirmed_at`).
- Laut Herstellerseite entwickelt und produziert Yellotools in Windeck; genannte Zielgruppen:
  Werbetechniker, Fahrzeugfolierer, Digitaldrucker, Display- und Beschilderungsspezialisten.
- Nicht pauschal übernommen: Jede Gruppe nennt Materialien, Prozesse, typische und **alternative
  Anwender (Hypothesen)** sowie **bekannte Einschränkungen** (z. B. Handwerkzeuge ersetzen keine
  Serienmaschinen; Wirksamkeitsangaben zur Antistatik sind Herstellerangaben).
- Lücken: Für die PPF-Rakel fehlen Details (Härte, Maße); die vollständige Kategorienstruktur
  und der Artikelstamm liegen nicht vor. Die Matrix ersetzt keinen Artikelstamm.

### 1a. Abgleich mit yellotools.com (2026-10-25)

Am 2026-10-25 war yellotools.com erreichbar (2026-10-23 noch gesperrt – respektiert). Geprüft
wurden je Produktgruppe die genannten Quellseiten und die offizielle Sitemap; jedes
Beispielprodukt wurde wörtlich gesucht. **38 von 44 Beispielnamen sind auf offiziellen Seiten
belegt** (Felder `examples_on_manufacturer_site`, `examples_not_found`, `source_checked_at`);
6 Produktseiten wurden als Quellen ergänzt, nichts gelöscht oder umbenannt. Nicht gefunden:
AllStar Gold, AllStar Cutter, YelloFlex-Größenangaben, YelloFlex Shape, AntiStatic Fluid C/E
(nur „C“), StreetRoller Basic. **Geprüft wurden nur Namen und Quellen**, nicht Funktionen,
Materialien oder Grenzen. Der Status bleibt `researched`; die Anwendung zeigt überall
„NICHT verifiziert – 0 von 12 Produktgruppen vom Hersteller/Vertrieb bestätigt; …“.
Protokoll: `docs/proof/2026-10-25_phase6_rollen_bewertung.md` §5.

| Produktgruppe | Kern-Prozess | Wichtigste Grenze | Herstellerquelle (Beispiel) |
|---|---|---|---|
| Kunststoffrakel und Rakelsets | Trockenverklebung | harte Kante kann verkratzen | https://www.yellotools.com/en/Tools/Squeegees/ |
| Rakel für Nassverklebung/Glas | Fensterfolierung | Gummilippe verschleißt | https://www.yellotools.com/en/Tools/Squeegees/Wooden-Squeegees/ |
| Rakel für Lackschutzfolie | PPF-Montage | Details unvollständig | https://www.yellotools.com/en/p/ppf-sandwich-multi-layer-squeegee-for-paint-protection-films/YT20PPFS04 |
| Car-Wrapping-Detailwerkzeuge | Kanten, Fugen, Demontage | auf Fahrzeuggeometrie ausgelegt | https://www.yellotools.com/shop/en/car-wrapping/ |
| Heißluft und Wärmestrahler | Tempern/Nachwärmen | allgemeiner Wettbewerb | https://www.yellotools.com/shop/en/car-wrapping/tempering-post-heating/ |
| Messer, Cutter, Lineale, Matten | Schnitt ohne Oberflächenschaden | keine Serienfertigung | https://www.yellotools.com/en/Tools/Cutting/ |
| Möbel-/Interiorfolierung | 90°-Kanten | Rundungen eingeschränkt | https://www.yellotools.com/en/Tools/Furniture-wrapping/ |
| Entschriften | Folien/Aufkleber/Klebereste entfernen | Untergrund vorher prüfen | https://www.yellotools.com/en/Tools/Removal/ |
| Reinigung/Antistatik | Untergrund, Kunststoffplatten | Herstellerangaben | https://www.yellotools.com/en/p/antistatic-fluid-professional-cleaning-fluid/YT12ASFC100M |
| Magnetlösungen | Positionieren | nur magnetische Untergründe/Pads | https://www.yellotools.com/shop/en/labeling/magnetic-solutions/ |
| Wand- und Bodenroller | Wand-/Bodengrafik | Bodenmarkierung je Material prüfen | https://www.yellotools.com/en/Tools/Labeling/Floor-and-Wall-Rollers/ |
| Applizieren/Kaschieren | Applikationstape, Kaschieren von Hand | Kleinserien | https://www.yellotools.com/shop/en/applying/ |

## 2. Anwendungsfälle und Branchen

16 Anwendungsfälle aus der Branchenliste des Auftrags (`origin = "auftrag"`) und 5 aus dem
Portfolio abgeleitete (`origin = "abgeleitet"`): Entfolierung bei Flotten (Leasing,
Vermietung, Handel, ÖPNV), Gebäudereinigung/Facility-Management (Aufkleberentfernung),
Acrylglas-/Kunststoffplattenverarbeitung (Antistatik), Boden-/Flächenmarkierung mit Folie,
und – aus der Auftragsliste konkretisiert – Beschriftung von Maschinen und Anlagen.

**Alle 21 sind Suchhypothesen** (`evidence_status = "hypothesis"`), keine Aussage, dass die
Branche geeignet ist. Jeder Eintrag enthält Begründung, Gegenindizien, die zugeordneten
Produktgruppen und Prozesse sowie spezifische Belegbegriffe (`website_terms`). Ein
Unternehmen wird nur bewertet, wenn einer dieser Begriffe wörtlich in seinen gespeicherten
Dokumenten steht.

## 3. Pflegeprozess

```bash
mi products list                      # Übersicht
mi products show rakel_ppf            # eine Gruppe vollständig
mi products validate                  # Pflichtfelder, URLs, Querverweise Produkt ↔ Anwendungsfall
mi products export-csv produkte.csv   # Vorlage für Excel/LibreOffice (Listen mit ";")
mi products import-csv produkte.csv --out meine_produkte.toml   # Zeilen ersetzen/ergänzen, prüfen
# in market_intel.toml: products_file = "meine_produkte.toml"
#                       use_cases_file = "meine_anwendungsfaelle.toml"   (Kopie von use_cases.toml)
#                       opportunity_file = "mein_modell.toml"            (Kopie von opportunity.toml)
mi opportunity-model                  # Gewichte, Segmente, Prüfung
```

Prüfregeln (`products.validate`, ergänzt in Phase 6: Abgleichlisten nur mit Namen aus
`examples`, nicht zugleich gefunden und nicht gefunden, mit `source_checked_at`): Pflichtfelder (Name, Beschreibung, Materialien, Prozesse,
typische Anwender, Quellen); jede Quelle beginnt mit `https://`, `http://` oder `intern:`;
`confirmed` nur mit `confirmed_by`/`confirmed_at`; alle referenzierten Anwendungsfälle und
Produktgruppen existieren; jeder Anwendungsfall hat Produktgruppen und Belegbegriffe.

Empfohlener Ablauf mit Yellotools: (1) Vertrieb prüft je Anwendungsfall „bestätigt /
verwerfen“ – verworfene bleiben mit `rejected` dokumentiert und werden nicht mehr bewertet;
(2) Produktmanagement prüft Grenzen und alternative Anwender je Produktgruppe; (3) Artikelstamm
(Artikelnummern) als zusätzliche Spalte ergänzen, sobald verfügbar.

Bekannte Einschränkung der CSV-Pflege: Listeneinträge, die selbst ein „;“ enthalten (z. B. bei
`limitations`), werden beim Re-Import in zwei Einträge geteilt („;“ ist das Listentrennzeichen).
Solche Einträge vor dem Export umformulieren oder direkt im TOML pflegen.

## 4. Rollen und Belegstatus (Phase 6)

Regeln: `src/market_intel/data/roles.toml` (pflegbar), Umsetzung `opportunity/roles.py`.

- **Direkter Beleg ist nur ein Tätigkeitssatz**: ganzer Satz (mindestens 6 Wörter, Satzzeichen am
  Ende) aus eigenen Angaben, in dem das Unternehmen über sich spricht (wir/unser/„das
  Unternehmen“/Firmenname) oder die Leistung am Satzanfang nennt („Montage von …“). Menüpunkte,
  Kategorien, Produktnamen, Jobtitel, Produktsätze („engineered for …“), Zielgruppensätze
  (Installateur-Netzwerk, Schulung) und Sätze über andere Firmen zählen **nicht**.
- **Rollen** Verarbeiter/Installationsbetrieb, Hersteller, Händler/Distributor werden **je für
  sich** belegt; mehrere Rollen sind möglich (Umdasch: Montage und Fertigung). Ohne Satzbeleg
  heißt die Rolle „unklar“ – das ist keine Aussage, dass die Rolle fehlt.
- **Belegstatus** je Anwendungsfall: belegt (Tätigkeitssatz + eigene Verarbeitung) · hinweis ·
  widersprüchlich · unklar · kein Beleg · keine Daten. Nur „belegt“ erreicht die Produkt-Fit-
  Segmente; hinweis/widersprüchlich/unklar landen in „Manuelle Prüfung erforderlich“ (sichtbar,
  exportiert, nicht ausgeschlossen); „keine Daten“ (z. B. gesperrte Website) ist ausdrücklich
  kein „kein Bedarf“.
- **Hersteller ohne belegte Verarbeitung** (ORAFOL, Avery Dennison im Live-Test) erhalten weder
  einen hohen Fit noch einen Ausschluss: Werkzeugbedarf (Anwendungstechnik, Musterbau,
  Schulung, Partner-/Kanalrolle) ist unbekannt und muss der Vertrieb klären.
