# Yellotools-Produkt- und Anwendungsmatrix

Stand: 2026-10-22 · Dateien: `src/market_intel/data/products.toml` (12 Produktgruppen),
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

| Produktgruppe | Kern-Prozess | Wichtigste Grenze | Herstellerquelle (Beispiel) |
|---|---|---|---|
| Kunststoffrakel und Rakelsets | Trockenverklebung | harte Kante kann verkratzen | https://www.yellotools.com/en/Tools/Squeegees/ |
| Rakel für Nassverklebung/Glas | Fensterfolierung | Gummilippe verschleißt | https://www.yellotools.com/en/Tools/Squeegees/Wooden-Squeegees/ |
| Rakel für Lackschutzfolie | PPF-Montage | Details unvollständig | https://www.yellotools.com/en/Tools/Removal/ |
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

Prüfregeln (`products.validate`): Pflichtfelder (Name, Beschreibung, Materialien, Prozesse,
typische Anwender, Quellen); jede Quelle beginnt mit `https://`, `http://` oder `intern:`;
`confirmed` nur mit `confirmed_by`/`confirmed_at`; alle referenzierten Anwendungsfälle und
Produktgruppen existieren; jeder Anwendungsfall hat Produktgruppen und Belegbegriffe.

Empfohlener Ablauf mit Yellotools: (1) Vertrieb prüft je Anwendungsfall „bestätigt /
verwerfen“ – verworfene bleiben mit `rejected` dokumentiert und werden nicht mehr bewertet;
(2) Produktmanagement prüft Grenzen und alternative Anwender je Produktgruppe; (3) Artikelstamm
(Artikelnummern) als zusätzliche Spalte ergänzen, sobald verfügbar.
