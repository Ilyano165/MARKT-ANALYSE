# Forensischer Projekt-Audit – Yellotools Market Intelligence (Repository MARKT-ANALYSE)

Audit-Datum: 2026-11-06 · Branch `claude/confident-faraday-0tsww5` · Commit `df50d4f` (identisch mit
`origin`, Arbeitsverzeichnis vor dem Audit sauber) · Version 0.5.0 · Schema 5

**Vorgehen.** Am Code wurde nichts geändert; einzige neue Datei ist dieser Bericht (kein
Commit, kein Push). Alle Prüfungen liefen in einem Scratchpad mit Kopien:
- **Anwendung im Browser (Chromium/Playwright):** zwei Instanzen –
  - eine frische Installation über den Desktop-Starter, derselbe Code wie `MarketIntel.exe`,
  - eine Kopie der echten Live-Datenbank vom 2026-10-23 (516 Firmen).
- **Echter Rechercheauftrag** gegen das Internet, auf einer leeren Testdatenbank, mit hartem
  Abbruch und Fortsetzung.
- **Testsuite, Lint und Typprüfung** neu ausgeführt; nichts aus früheren Berichten übernommen.
- **Windows-Build:** Die Angaben stützen sich auf die GitHub-Actions-Protokolle (echter
  Windows-Rechner, Lauf 42223825633 vom 2026-11-06); ein eigener Windows-PC stand nicht zur
  Verfügung.

Begriff „BAT-Datei“: Laut Nutzer ist das schwarze Konsolenfenster (cmd) gemeint, das beim Start
erscheint.

---

## 1. Executive Summary

Das Projekt ist ein **technisch sorgfältiger Kern mit unfertigem Produkt darum herum**.

**Was trägt:**
- Datenmodell, Herkunftsnachweise, Abrufschicht (robots.txt, Drosselung, Sperren, Cache),
  Bewertungslogik und Sicherheitsgrundlagen sind solide und gut getestet.
- 240 Tests: 237 bestanden, 3 übersprungen; `ruff` und `mypy --strict` ohne Befund.

**Was der Nutzer erlebt, ist dagegen in mehreren Punkten defekt oder unvollständig:**

1. **Jede Speicher-Aktion der Oberfläche scheitert im echten Browser (P0).** Notiz,
   Watchlist-Status, Vertriebsergebnis und Wettbewerbseinordnung liefern 403 „Fremde Herkunft“.
   Ursache: Der Server setzt `Referrer-Policy: no-referrer`, Chromium-Browser (auch Edge/Chrome
   unter Windows) senden dadurch `Origin: null`, und die eigene Herkunftsprüfung lehnt `null`
   ab. Die automatischen Tests haben das nicht bemerkt, weil sie Formulare ohne Origin-Header
   absenden.
2. **Die Oberfläche kann keine Recherche starten (P1).**
   - Es gibt keinen Auftrag, keinen Fortschritt, keinen Import und keinen Upload in der
     Oberfläche.
   - 28 Funktionen gibt es nur als Kommandozeilenbefehle.
   - Nach der Installation zeigt die App eine leere Tabelle und verweist auf `mi research`.
3. **Die automatische Recherche liefert fachlich unbrauchbare Ergebnisse (P1).**
   - **Echter Auftrag** „Finde Fahrzeugfolierer in Deutschland“: 7 Minuten, 30 Abrufe,
     24 Firmen.
   - **Ohne Grundlage:** keine einzige Firma mit Website, keine Umsatz- oder Mitarbeiterzahl.
   - **Falsche Auswahl:** 4 der 5 ausgewählten Firmen sind Behörden – Auftraggeber aus
     EU-Ausschreibungen, die Folierung *einkaufen*.
4. **Die Lead-Liste zeigt veraltete, inzwischen als falsch erkannte Bewertungen (P1).**
   - Avery Dennison steht dort mit „Fit 81, hoher Produkt-Fit“; die Opportunity-Seite derselben
     Firma zeigt „Fit 40, manuelle Prüfung“.
   - Neu bewertet wird nur per Kommandozeile.
5. **Keine eigenständige Desktop-Anwendung.** Es gibt eine echte, signaturlose `MarketIntel.exe`
   mit Installer. Sie öffnet aber ein Konsolenfenster und die Oberfläche im Standardbrowser. Das
   ist eine **lokale Webanwendung im externen Browser** – so übrigens auch in der eigenen
   Architekturentscheidung ADR-03 geplant, aber nicht das, was der Nutzer erwartet.
6. **Visuelle Referenzen liegen im Repository nicht vor.** Ein Abgleich ist NICHT VERIFIZIERT.
   Die Oberfläche ist schlicht, funktional und gestalterisch nicht auf Produktniveau.

**Urteil:** Eine nutzbare Marktanalyse-Anwendung für Vertriebsmitarbeiter unter Windows liegt
**nicht** vor. Ein Entwickler kann per Kommandozeile Recherchen ausführen und Belege
nachvollziehen; ein Vertriebsnutzer kann mit der installierten App heute praktisch nur
vorhandene Daten ansehen.

---

## 2. Tatsächlicher Projektzustand

| Merkmal | Befund (geprüft) |
|---|---|
| Sprache/Laufzeit | Python ≥ 3.12; 20 184 Zeilen in `src/`, 5 241 Zeilen Tests |
| Abhängigkeiten | httpx, SQLAlchemy 2 (Core), openpyxl, pypdf; optional psycopg |
| Backend | eigener HTTP-Server auf `http.server` (`ui/server.py`, 484 Zeilen), kein Framework |
| Frontend | serverseitig erzeugtes HTML (`ui/render.py`, 1 077 Zeilen), 160 Zeilen CSS, 282 Zeilen JS (Reiter, Diagramm, Weltkarte); kein SPA-Framework |
| Datenbank | SQLite (WAL), PostgreSQL-DDL vorhanden, gegen PostgreSQL **NICHT VERIFIZIERT** |
| Recherche | `research.py` (Aufgabenkette, 8 Schritte), `engine/` (Strategie, Adapter, Extraktion, Abgleich, Bericht), `sources/` (12 Quellenmodule), `http/gateway.py` (zentraler Abruf) |
| Bewertung | `opportunity/` (Belegklassen, Rollen, Bewertung, Segmente, Leads, Vertriebsergebnisse) |
| Hintergrundprozesse | **keine.** Recherche läuft im Vordergrund des aufrufenden Kommandozeilenprozesses, sequenziell |
| Startmechanismus | `MarketIntel.exe` (PyInstaller) → `market_intel.desktop` → `ui`-Server + Browser; Kommandozeile `mi` bzw. `mi.cmd` |
| Installer/Build | NSIS-Setup, gebaut und geprüft per GitHub Actions auf Windows (`.github/workflows/windows.yml`) |
| CI | nur der Windows-Workflow (Tests + Build + Installationstest); kein Linux-CI |
| Doku | README, Spezifikation, Architektur, Datenmodell, Roadmap, Quellen, Sicherheit, Prüfprotokolle |

---

## 3. Tatsächliche Anwendungsform

| Form | Liegt vor? | Nachweis |
|---|---|---|
| Native Desktop-Anwendung | **nein** | kein GUI-Toolkit im Code |
| Desktop-App mit eingebettetem Webinterface | **nein** | kein WebView (z. B. pywebview, Electron) |
| **Lokale Webanwendung im externen Browser** | **ja** | `desktop.py` startet den Server auf 127.0.0.1 und ruft `webbrowser.open` |
| Entwicklungsserver | nein | eigener `ThreadingHTTPServer`, keine Debug-Endpunkte gefunden |
| Bloßes Startskript | nein | Die Startmenü-Verknüpfung zeigt auf `MarketIntel.exe`; nur der zweite Eintrag (Eingabeaufforderung) startet `cmd.exe` |

Das „BAT-Gefühl“ hat eine konkrete Ursache: `installer/MarketIntel.spec:25` setzt
`console=True`, daher erscheint ein schwarzes Konsolenfenster mit Adresse und Datenordner.
Schließt man es, endet die Anwendung.

---

## 4. Architekturübersicht – tatsächlicher Weg

| Stufe | Komponente / Dateien | Implementiert | Wird aufgerufen | Mit nächster Stufe verbunden | Nachweislich funktionsfähig |
|---|---|---|---|---|---|
| Benutzeraktion | Browser-Oberfläche; Kommandozeile `mi` | ja | ja | – | – |
| UI | `ui/render.py`, `ui/static/*` | ja (nur Anzeige, Suche, 4 Formulare) | ja | Lesen ja, **Schreiben nein (403)** | Lesen: ja (64 Seiten ohne JS-Fehler); Schreiben: **DEFEKT** |
| API | `ui/server.py` (13 Routen, kein JSON-API außer `/company/{id}.json`) | ja | ja | ja | GET ja; POST aus Browser **DEFEKT** |
| Geschäftslogik | `opportunity/*`, `intel/*` | ja | aus UI nur lesend und rechnend | ja | ja (Tests, Kopie-DB) |
| Recherche-Engine | `research.py`, `engine/*` | ja | **nur über `mi research`/`mi resume`** | **nicht mit der UI verbunden** | läuft (echter Lauf, siehe §8), Ergebnisqualität mangelhaft |
| Datenquellen | `sources/*`, `http/gateway.py` | ja | ja | ja | GLEIF/TED ok; Wikidata 403 gesperrt; GDELT 14 Netzfehler, 5 HTTP-Fehler; Websites mangels Domains nicht abgerufen |
| Verarbeitung | `engine/extract.py`, `extraction/*` | ja | ja | ja | im echten Lauf: 0 Dokumente ausgewertet (keine Websites) |
| Datenbank | `db.py`, `repo.py` | ja | ja | ja | ja (Persistenz über harten Abbruch nachgewiesen) |
| Ergebnisdarstellung | UI-Profile, Opportunity, Leads; Bericht als Datei | ja | ja | **Lead-Liste liest veraltete Bewertungen** | **teilweise falsch** |

**Nicht verbundene oder nur intern genutzte Teile:**

| Teil | Zustand |
|---|---|
| Dublettenkandidaten `engine/matching.py` | werden erzeugt, aber weder per UI noch per Kommandozeile zusammenführbar |
| Rechercheberichte | `bericht.md`, `bericht_auftrag.html` liegen nur als Dateien im Ausgabeordner, nicht in der UI |
| Änderungserkennung | nur im Profil-Reiter „Historie“, keine Benachrichtigung |
| Vertriebsergebnis-Auswertung | nur per Kommandozeile |

**Weitere Befunde:**
- **Mock-Daten:** keine in Produktionspfaden. Alle Tests mit Netzbezug nutzen das Testdouble
  `tests/fakeweb.py` mit erfundenen `.test`-Domains – korrekt getrennt.
- **Platzhalter, die Nutzer sehen:** keine. Aber der Hinweistext auf der Startseite verweist auf
  Kommandozeilenbefehle (`render.py:172`).

---

## 5. Windows-Setup und Distribution

| Prüfpunkt | Befund | Nachweis |
|---|---|---|
| Was installiert der Installer? | `MarketIntel.exe`, Ordner `_internal` (Python-Laufzeit, Bibliotheken, Daten), `mi.cmd`, `Uninstall.exe`; 2 Startmenü-Einträge; Eintrag unter „Apps & Features“ | `installer/MarketIntel.nsi`; CI-Protokoll |
| Anwendung oder Startskript? | echte EXE (PyInstaller-Bootloader + gebündelte Python-Laufzeit) | CI: `MarketIntel.exe` gebaut, gestartet, Version 0.5.0 |
| BAT-Datei nötig? | nein; `mi.cmd` nur für die Kommandozeilen-Verknüpfung | NSIS-Skript |
| Lokaler Webserver? | ja, 127.0.0.1, Port 8765 oder nächster freier | `desktop.py` |
| Nur Standardbrowser? | ja | `webbrowser.open` |
| Eigenes Fenster? | nur Konsolenfenster; Oberfläche im Browser-Tab | `console=True` |
| Symbol / Produktidentität | **kein eigenes Symbol** (PyInstaller-Standard), keine Versionsressource, Konsolentitel ohne Branding; Fenster-/Seitentitel „Market Intelligence“ | `.spec` ohne `icon=` |
| Ohne Python lauffähig? | ja | CI: Installation und Rauchtest auf sauberem Runner (das Setup-Python dort war nicht auf dem PATH der App; Bündel vollständig) |
| Start nach Neustart | kein Autostart, Start per Verknüpfung; Verhalten nach echtem Windows-Neustart **NICHT VERIFIZIERT** | – |
| Dienst läuft bereits | **keine Einzelinstanz-Prüfung**: zweiter Start öffnet zweiten Server und zweites Konsolenfenster auf derselben Datenbank | lokal reproduziert: zweite Instanz auf Port 8765 neben 8811 |
| Port belegt | stiller Ausweich auf freien Port (funktioniert) | `tests/test_desktop.py`, lokal |
| Fehlende Konfiguration | wird beim ersten Start angelegt (absolute Pfade, nie überschrieben) | lokal: `market_intel.toml` erzeugt |
| Verständliche Fehler | Browser zeigt „Interner Fehler (Details im Terminal)“; Details nur im Konsolenfenster | `server.py` |
| Updates / Deinstallation | Setup darüber installieren; Deinstallation entfernt Programm, Verknüpfungen, Registry, behält Daten | CI-Installationstest |
| Signatur | **unsigniert** → SmartScreen-Warnung | – |
| Bezug | nur als GitHub-Actions-Artefakt (Anmeldung nötig, 30 Tage gültig); Direktlinks laufen nach Minuten ab | Nutzer erhielt 404 |

**Bewertung:** Lokale Webanwendung im externen Browser mit Konsolenfenster. Technisch sauber
verteilt (EXE, Installer, Deinstallation geprüft). Von einer „eigenständigen, professionell
nutzbaren Windows-Anwendung“ entfernt durch:
- kein eigenes Fenster,
- Konsolenfenster,
- kein Symbol,
- keine Einzelinstanz-Prüfung,
- keine Signatur,
- kein dauerhafter Download.

---

## 6. UI-/UX-Audit

**Referenzen:** Im Repository gibt es keine Screenshots, Mockups oder UI-Spezifikationen
(gesucht nach Bilddateien sowie den Stichworten „Design/Mockup/Screenshot/Referenz“). Ein
Abgleich mit den „zuvor erstellten visuellen Referenzen“ ist **NICHT VERIFIZIERT**; sie
liegen diesem Projekt nicht vor.

**Gesamteindruck (Screenshots 1440 px und 390 px, beide Instanzen):**

| Bereich | Befund |
|---|---|
| Visuelle Qualität | schlichtes Dokument-Layout (Systemschrift, grauer Hintergrund, blaue Links und Knöpfe), funktional, nicht auf Produktniveau |
| Navigation | nur „Unternehmen“ und „Leads“; keine Aufträge, kein Import, keine Einstellungen |
| Informationshierarchie | Firmenprofil gut gegliedert (9 Reiter); Lead-Liste überladen |
| Statusanzeige Leads | Der Text „nicht ermittelt (Bewertung vor Modell v3 – neu bewerten)“ steht zweimal je Zeile |
| Interne Codes im Klartext | z. B. „1.200 Personen (2026, unknown, company_reported)“ statt deutscher Bezeichnungen |
| Startseite | Liste aller 516 Firmen ohne Paginierung (Seitenhöhe 27 356 px) |
| Responsivität | kein horizontaler Überlauf auf 390 px (gemessen) |
| Lade- und Leerzustände | kein Ladezustand nötig (Server rendert); Leerzustände vorhanden („keine Treffer“, „unbekannt“) |

**UI-Elemente (im Browser tatsächlich bedient):**

| Element | Kategorie | Nachweis |
|---|---|---|
| Menü „Unternehmen“, „Leads“ | funktioniert vollständig | Navigation |
| Suche Name/Domain | funktioniert vollständig | „Umdasch“ → 1 Treffer; „umdasch.com“ → 1 Treffer |
| 9 Profil-Reiter | funktioniert vollständig | je genau ein Abschnitt sichtbar |
| Zeitraum + „Schätzungen einbeziehen“ + „Anzeigen“ | funktioniert vollständig | URL `?years=5&est=1` |
| Diagramm-Umschalter (Werte / % / absolut) | funktioniert (3 Schalter gezeichnet) | Umdasch, ORAFOL |
| Weltkarte Standorte | funktioniert (222 Pfade gezeichnet) | Umdasch |
| Links Quelle/Dokument/Herkunft | funktioniert | Crawl ohne Fehler |
| JSON-Link | funktioniert | HTTP 200 |
| **Notiz speichern** | **funktioniert nicht** | Browser: 403 „Fremde Herkunft“; per curl ohne Origin: 303 ok |
| **Notiz löschen** | **funktioniert nicht** (gleiche Ursache) | – |
| **Watchlist-Status speichern** | **funktioniert nicht** | Browser: 403 |
| **Vertriebsergebnis speichern** | **funktioniert nicht** (gleiche Prüfung) | Formular vorhanden, POST mit `Origin: null` → 403 |
| **Wettbewerb „Einordnung speichern“** | **funktioniert nicht** | Browser: 403; 0 gespeicherte Einordnungen |
| Wettbewerbsanalyse (Ansicht) | liefert unvollständige / unbrauchbare Daten | listet alle 515 übrigen Firmen der Datenbank als zu prüfende Vergleiche |
| Lead-Segmentfilter | funktioniert technisch | 11 Segmente, 2 mit Treffern |
| Lead „CSV herunterladen“ | funktioniert | Datei `leads.csv` |
| **Lead-Liste (Inhalt)** | **liefert falsche Daten** | zeigt v2-Bewertungen (Avery Fit 81), Opportunity-Seite zeigt v3 (Fit 40) |
| Opportunity-Seite | funktioniert (Anzeige, rechnet live) | Avery 40 / hinweis, ORAFOL 20 / unklar, Sixt keine_daten |
| Wachstumshinweise im Profil | liefert doppelte Daten | Avery: jede Pressemeldung zweimal (als Signal und als Ereignis gespeichert und beide angezeigt) |
| Recherche starten / Fortschritt / Abbrechen | **nicht vorhanden** | keine Route |
| Firmenliste importieren, Dokument hochladen, Kennzahl erfassen | **nicht vorhanden** | nur Kommandozeile |
| Dubletten zusammenführen | **nicht vorhanden** | – |

---

## 7. Funktionsaudit

### A. Unternehmensrecherche

| Funktion | Stand | Automatisch? | Nachweis |
|---|---|---|---|
| Unternehmen finden | GLEIF-Namenssuche, TED-Ausschreibungen, Wikidata, Nutzerlisten | automatisch, aber **TED liefert Auftraggeber statt Anbieter** | echter Lauf: 23 von 24 Treffern aus TED, 4 von 5 Auswahlen Behörden |
| Eindeutig identifizieren | LEI-basiert, Mehrdeutigkeit offen ausgewiesen | automatisch | Live-Test Phase 5; Tests |
| Rechtsnamen, Konzern | GLEIF Level 2 | automatisch | Avery: offizieller Name, Mutter „keine belegt“ |
| Website/Domain zuordnen | nur über Wikidata (aus der Cloud gesperrt) oder Nutzerliste | **im echten Lauf 0 von 24** | „keine Website bekannt (keine Websuche verfügbar)“ |
| Standorte | aus Register und Websites | teilweise | Umdasch-Karte; im neuen Lauf keine |
| Tätigkeiten | nur aus Website-Texten | hängt an Website (siehe oben) | – |
| Branchen | Wikidata/Register; Avery: „Branche unbekannt“ | teilweise | Profil |
| Mitarbeiter, Umsatz | SEC, Websites, Dokument-Upload | **im echten Lauf 0 Werte** | `financial_observation` = 0, `employee_observation` = 0 |
| Länder/Präsenz | Belegstufen implementiert | teilweise | – |
| Wachstum/Veränderungen | Zeitreihen, Änderungserkennung | implementiert; ohne Daten wirkungslos | – |
| Expansion/Standorte | Pressetexte (Regeln) | teilweise; Duplikate | Avery-Profil |
| Stellenanzeigen | Karriereseiten der Website | an Website gebunden | – |
| Wettbewerber | Einordnung gegen **alle** Firmen der Datenbank | **nicht sinnvoll automatisiert** | 515 Vergleichszeilen |

### B. Recherche-Engine

| Merkmal | Befund |
|---|---|
| Aufträge, Strategie, Quellenzugriff | vorhanden |
| Extraktion, Quellenbelege, Zitatprüfung | vorhanden |
| Fehlerbehandlung, Wiederholungen, Rate-Limits, Timeouts, Sperren | vorhanden und getestet (`http/gateway.py`, 12 Tests) |
| Parallelisierung | **keine** |
| Fortschritt | nur in der Konsole bzw. per `mi status` |
| Abbruch und Fortsetzung | funktioniert, nachgewiesen: harter Abbruch, `mi resume`, Ende `completed_with_gaps` |
| Cache und Protokoll (`fetch_log`) | vorhanden |
| Neue Informationen? | Ja, die Engine ruft wirklich ab. Im realen Lauf entstanden daraus aber fast nur Namen ohne Inhalte. |
| Fehler im Bericht | zählt die Abrufe nur der letzten Sitzung („Netzabrufe: 15“, tatsächlich 30); deutet „Finde Fahrzeugfolierer“ als Marktführer-Rangliste (leer) |

### C. Produkt-Fit

**Was implementiert ist:**
- Belegklassen: Satz, Selbstbezug, Produkt-, Zielgruppen- und Fremdsatz.
- Rollen Verarbeiter/Hersteller/Händler mit eigenen Satzbelegen.
- Verneinung, auch nach dem Verb.
- Deckel nach Belegstatus.
- Bestandskunden- und Ausschlusslogik.
- Die Segmente „keine Daten“ und „manuelle Prüfung“.

**Prüfung an den sechs Fällen** (Opportunity-Seite, Kopie der Live-Datenbank, live
gerechnet):

| Firma | Ergebnis | durch gespeicherte Quellen belegt? |
|---|---|---|
| Avery Dennison | Fit 40 / hinweis | ja, Zitate stehen im Dokument (Test prüft das) |
| ORAFOL | Fit 20 / unklar | ja |
| Sixt | keine_daten | ja |
| Umdasch | belegt | ja |
| Steinel | 0 / kein_beleg | ja |
| Deutsche Bank | 0 / kein_beleg | ja |

**Aber:** Die **Lead-Liste** zeigt für dieselben Firmen die alten v2-Werte (Avery 81,
ORAFOL 53), weil die gespeicherten Bewertungen nie neu berechnet wurden.

### D. Wettbewerbsanalyse

Es gibt keine automatische Identifikation, keinen Produkt- oder Angebotsvergleich und keine
historischen Größenvergleiche. Die Ansicht ordnet jede andere Firma der Datenbank ein; bei
516 Firmen sind das 515 Zeilen. Speichern der Einordnung ist im Browser defekt (403).

### E. Produktmatrix und Opportunity

- **Speicherung:** Die Matrix liegt in `data/products.toml` (12 Gruppen) mit Abgleich
  2026-10-25 (38 von 44 Beispielnamen auf Herstellerseiten gefunden).
- **Verifikation:** Status „NICHT verifiziert“ wird in Bericht, Opportunity-Seite und CLI
  ausgewiesen. In der Lead-Liste der Kopie-Datenbank erscheint statt des Matrixstands
  „nicht ermittelt“ (alte Bewertungen).
- **Bewertungen:** nachvollziehbar (Teilwerte, Begründungen, Zitate).

### F. Bestandskunden und Leads

| Funktion | Befund |
|---|---|
| Listenimport | nur Kommandozeile |
| Dubletten über LEI/Domain | getestet |
| Bestandskundensegment, Ausschluss verworfener Firmen | getestet |
| Lead-Export CSV/XLSX/JSON | funktioniert; die Oberfläche bietet nur CSV |
| Persistenz | SQLite-Datei im Benutzerordner, über Neustart erhalten (getestet) |
| Verwerfen | in der Oberfläche **defekt** (Watchlist-Formular 403) |

---

## 8. Automatisierungs- und Rechercheaudit

Echter, kontrollierter Ablauf:
- **Auftrag:** `mi research "Finde Fahrzeugfolierer in Deutschland" --country DE --use-case
  fahrzeugfolierung --max-candidates 5 --max-requests 200 --max-seconds 600 --no-llm`
- **Umgebung:** leere Testdatenbank, ohne Kontaktadresse im User-Agent.
- **Ablauf:** nach rund 2 Minuten hart abgebrochen (SIGKILL), danach mit `mi resume 1`
  fortgesetzt; der fortgesetzte Teil dauerte 283 s.

| # | Frage | Ergebnis | Einstufung |
|---|---|---|---|
| 1 | Auftrag erstellen | nur per Kommandozeile | manuell unterstützt |
| 2 | Branche, Region, Zielgruppe | Freitext, Land, Branche, Anwendungsfall, Listen | teilweise automatisiert |
| 3 | Aufgaben ableiten | Strategie und 42 Aufgaben erzeugt; Absicht falsch gedeutet („market_leaders“) | teilweise automatisiert |
| 4 | Unternehmen suchen | 24 gefunden (GLEIF 1, TED 23) | teilweise – überwiegend Behörden |
| 5 | Quellen abrufen | 30 Abrufe: GLEIF 4 ok / 2 Fehler, TED 2 ok, Wikidata gesperrt, GDELT 19 Fehler | teilweise |
| 6 | Informationen extrahieren | 0 Dokumente ausgewertet („keine Dokumente zur Auswertung“) | **scheitert hier** |
| 7 | Bewerten | 5 Firmen, alle „manuelle Prüfung“, Fit 27 | formal ja, inhaltlich unbrauchbar |
| 8 | Speichern | ja | vollständig automatisiert |
| 9 | Neue Unternehmen erkennen | ja (24 neu) | vollständig automatisiert |
| 10 | Anzeige in der Oberfläche | Firmen erscheinen in der Liste; Auftrag, Bericht und Fortschritt nicht | teilweise |
| 11 | Pausieren und Fortsetzen | Kommandozeile: ja (Strg+C bzw. Abbruch, `mi resume`) | vollständig (CLI) / nicht vorhanden (UI) |
| 12 | Fortschritt nach Neustart | erhalten (7 erledigte, 31 offene Aufgaben nach SIGKILL) | vollständig automatisiert |

**Scheiterpunkt:** zwischen Schritt 4 und Schritt 6.

1. **Website fehlt.** Ohne Website gibt es keine Tätigkeitsbelege. Die einzige automatische
   Websitequelle, Wikidata, sperrt Abrufe aus dieser Umgebung. Eine Websuche ist bewusst nicht
   vorgesehen (ADR-13, kostenfrei).
2. **TED liefert die Gegenseite.** Ausschreibungen bringen die Auftraggeber statt der Anbieter.

---

## 9. Datenbank- und Persistenzaudit

| Frage | Befund |
|---|---|
| Datenbank | SQLite (WAL); PostgreSQL nicht getestet |
| Aufträge und Ergebnisse persistent | ja (nachgewiesen) |
| Beobachtungen unveränderlich | ja, Trigger (Kennzahlen, Belege, Vertriebsergebnisse) |
| Historie vergleichbar | ja: jede Bewertung eine neue Zeile, Änderungserkennung |
| Doppelt gespeichert | **ja:** 88 Gruppen identischer Signale aus demselben Dokument (197 Zeilen) in der Live-Datenbank; Pressemeldungen zusätzlich als Signal **und** Ereignis |
| Transaktionen | ja (`engine.begin()`) |
| Warteschlange | persistente Aufgabentabelle (`research_task`) |
| Crash-Recovery, Wiederaufnahme | ja, per `mi resume` (nicht automatisch beim nächsten Start) |
| Fehler und Status gespeichert | ja (`research_task`, `fetch_log`, `investigation_log`) |
| Validierung | ja (Zitate, Werte, Währungen, Daten) |
| Backup/Wiederherstellung | **nicht implementiert** (NFR-09 offen) |
| Langfristige Historie | technisch ja; durch Datenqualität (Behörden, keine Websites) begrenzt |
| Altlast nach Modellwechsel | Lead-Liste liest alte Bewertungen (kein automatisches Neubewerten) |

---

## 10. Sicherheits- und Qualitätsaudit

**Geprüft und in Ordnung:**

| Prüfpunkt | Befund |
|---|---|
| Netzwerkbindung | nur 127.0.0.1 |
| Fremder Host-Header | 421 |
| Fremder Origin | 403 |
| CSRF-Token | Pflicht, sonst 403 |
| CSP, X-Frame-Options, nosniff | gesetzt |
| Skript-Injektion (XSS) | über die Suche escaped |
| Pfad-Traversal | 404 |
| SQL | SQLAlchemy Core; ein f-String-SQL nur für Migrationen mit festen Namen (`db.py:794`) |
| Geheimnisse | keine im Repository, keine persönliche E-Mail-Adresse; Konfiguration lehnt Geheimnisse ab |
| Debug-Endpunkte | keine |
| Testdaten im Produktionspfad | keine |
| Typen und Lint | `mypy --strict` und `ruff` ohne Befund |

**Problem:** Die Kombination `Referrer-Policy: no-referrer` (`server.py:149`) + Origin-Prüfung
(`server.py:324`) blockiert legitime Formularabsendungen echter Browser. Das ist ein
Sicherheitsmechanismus, der die Funktion zerstört.

**Qualität:**
- Code gut strukturiert und durchgängig typisiert.
- `render.py` erzeugt HTML per f-String: wartungsaufwendig, aber konsequent escaped (217 Aufrufe
  von `e()`).
- Logging nur auf die Konsole, keine Logdatei. Unter Windows gehen Fehlermeldungen beim
  Schließen des Fensters verloren.

---

## 11. Testergebnisse (neu ausgeführt, 2026-11-06)

| Lauf | Gesamt | Bestanden | Fehlgeschlagen | Übersprungen | Laufzeit |
|---|---|---|---|---|---|
| Testsuite (Linux) | 240 | 237 | 0 | 3 | 97 s |
| Windows-Testsuite (CI) | 240 | 238 | 0 | 2 | ca. 3 min |
| Live-Tests | 2 | – | – | 2 (nur mit `MI_LIVE=1`; im Audit nicht ausgeführt) | – |

Übersprungen unter Linux: die 2 Live-Tests und 1 Windows-Pfadtest. Die Windows-Zahlen stammen
aus dem CI-Protokoll, Lauf 42223825633.

**Einordnung der Tests:**

| Art | Umfang |
|---|---|
| Unit | Extraktion, Zeitreihen, Strategie, Gateway, Rollen, CSV, Starter (ca. 140) |
| Integration mit Testdouble | Adapter, End-to-End-Recherche, Opportunity, Phase 5/6 (ca. 90); alle mit `FakeWeb` |
| UI | 3 Tests über HTTP mit `urllib` – **senden keinen Origin-Header** und erkennen den 403-Defekt deshalb nicht |
| Echte Integration | nur die übersprungenen Live-Tests |
| Build und Installer | Windows-Workflow: Rauchtest und stille Installation/Deinstallation (prüft nur GET-Seiten) |

**Nicht getestet:**
- Formularbedienung im echten Browser
- Lead-Liste nach Modellwechsel
- Qualität der Kandidatensuche (Anbieter vs. Auftraggeber)
- PostgreSQL
- Neustart unter Windows
- Datensicherung

**Mindestens ein vollständiger Rechercheprozess:** durchgeführt, siehe §8.

---

## 12. Abgleich mit den ursprünglichen Anforderungen

Quelle: `PRODUCT_SPECIFICATION.md` (Muss-Anforderungen und zentrale Soll-Anforderungen)
sowie die Produkterwartung des Nutzers.

| Anforderung | Implementierungsstand | Technischer Nachweis | Praktischer Test | Bewertung | Fehlende Arbeit |
|---|---|---|---|---|---|
| Eigenständige Windows-App (Nutzererwartung) | EXE + Installer, Oberfläche im Browser, Konsolenfenster | `.spec`, `.nsi`, CI | CI-Installation ok | TEILWEISE | eigenes Fenster, Symbol, Einzelinstanz, Signatur |
| FR-A-01 Recherche Branche × Land mit Budget | nur Kommandozeile | `research.py` | echter Lauf | TEILWEISE | UI, Kandidatenqualität |
| FR-A-02 Ergebnisliste mit Umsatz, MA, Sitz, Konzern | Felder vorhanden | Profil | echter Lauf: 0 Werte | TEILWEISE | Datenbeschaffung |
| FR-A-03 Ranking nur auf Belegen | ja | Bericht | Ranking leer (korrekt mangels Daten) | BESTANDEN (Regel) | – |
| FR-A-07 Vergleich 2–10 Firmen | nur Bericht per CLI | `engine/report.py` | nicht in der UI | TEILWEISE | Vergleichsansicht |
| FR-B-03 Discovery Anwendungsfall × Land | ja | Adapter | 4 von 5 Auswahlen Behörden | DEFEKT | Anbieter- statt Auftraggeberlogik, Websitefindung |
| FR-B-04/10 Produkt-Fit nur mit Beleg | ja | Phase 6, 32 Tests | sechs Fälle korrekt (Opportunity-Seite) | BESTANDEN | – |
| FR-B-06 Priorisierung mit Zerlegung | ja | Opportunity-Seite | ja | BESTANDEN | – |
| FR-B-07/08 Bestandskunden / Verwerfen | Logik ja; Verwerfen in der UI defekt | Tests | Browser 403 | DEFEKT (UI) | Formularfehler beheben |
| FR-B-12 Watchlist, Kommentare, Lead-Export | Export ja; Watchlist/Kommentar in der UI defekt; Lead-Liste veraltet | Tests | Browser 403, Avery 81 statt 40 | DEFEKT | Formulare, Neubewertung |
| FR-C-01…08, 11–14 Profil, Zeitreihen, Standorte, Belege | ja | Profilseiten | 64 Seiten ohne Fehler | BESTANDEN (Anzeige) | Daten |
| FR-C-09 Notizen | Anzeige ja, Speichern defekt | – | Browser 403 | DEFEKT | – |
| FR-C-10 Dubletten zusammenführen/trennen | nur Kandidaten | `matching.py` | keine Bedienung | NICHT IMPLEMENTIERT | UI/CLI |
| FR-C-15 Wettbewerbsanalyse | Einordnung gegen alle Firmen | `competitors.py` | 515 Zeilen; Speichern 403 | TEILWEISE / DEFEKT | Auswahl, Speichern |
| FR-C-16 Änderungserkennung | ja | `changes.py` | Reiter „Historie“ | TEILWEISE | Benachrichtigung |
| FR-D-01 Automatisierte Recherche über Adapter | ja | 12 Quellen | echter Lauf | TEILWEISE | Website-Ermittlung, Ergebnisqualität |
| FR-D-02 Runs anlegen, pausieren, fortsetzen, Fortschritt live sichtbar | CLI ja, UI nein | `research.py` | SIGKILL + resume ok | TEILWEISE | UI |
| FR-D-03 Runs im Hintergrund, UI bedienbar, Neustart setzt fort | kein Hintergrundprozess, keine automatische Fortsetzung | – | – | NICHT IMPLEMENTIERT | Worker |
| FR-D-04/05/06 Limits, Drosselung, Ausfalltoleranz | ja | `gateway.py` | `completed_with_gaps` | BESTANDEN | – |
| FR-D-07 Dokument-Upload | nur Kommandozeile | `import-document` | – | TEILWEISE | UI |
| FR-D-10 Zeitgesteuerte Aktualisierung | nein | – | – | NICHT IMPLEMENTIERT | Zeitplanung |
| FR-E-03 Benachrichtigung | nein | – | – | NICHT IMPLEMENTIERT | – |
| FR-E-06 Export CSV/XLSX/JSON | ja (UI nur CSV) | `leads.py` | CSV ok | BESTANDEN | – |
| NFR-02 kein UI-Request wartet auf Quellen | ja | – | – | BESTANDEN | – |
| NFR-03 Neustart verliert nichts | ja | – | SIGKILL-Probe | BESTANDEN | – |
| NFR-06 Abdeckung ≥ 90 % | nicht gemessen | – | – | NICHT VERIFIZIERT | Messung |
| NFR-08 barrierearm | teilweise (Labels, Tastatur-Reiter) | – | – | NICHT VERIFIZIERT | Prüfung |
| NFR-09 Datensicherung | nein | – | – | NICHT IMPLEMENTIERT | – |
| UI entspricht visuellen Referenzen | Referenzen nicht im Repository | – | – | NICHT VERIFIZIERT | Referenzen bereitstellen |

---

## 13. Problemliste

### P0 – Kritisch

**P0-1 Alle Speicher-Formulare scheitern im echten Browser (403).**
- **Betroffen:** `ui/server.py:149` (`Referrer-Policy: no-referrer`), `ui/server.py:324`
  (Origin-Prüfung).
- **Ursache:** Chromium sendet unter `no-referrer` `Origin: null`; der Server wertet `null` als
  fremd.
- **Auswirkung:** Notizen, Watchlist/Verwerfen, Vertriebsergebnisse und Wettbewerbseinordnung
  sind in der installierten App unbenutzbar.
- **Korrektur:**
  - Referrer-Policy auf `same-origin` setzen oder `Origin: null` zusammen mit gültigem
    CSRF-Token akzeptieren.
  - Browser-E2E-Test ergänzen.
- **Abhängigkeiten:** keine.

### P1 – Hoch

**P1-1 Recherche und Datenpflege nur per Kommandozeile.**
- **Betroffen:** `ui/server.py` (keine Routen dafür).
- **Auswirkung:** Ein Nutzer der Windows-App kann keinen Auftrag starten, keine Liste
  importieren und keine Dokumente hochladen.
- **Korrektur:** Seiten für Auftrag, Fortschritt, Import und Upload.
- **Abhängigkeiten:** P1-3.

**P1-2 Kandidatensuche liefert Auftraggeber statt Anbieter.**
- **Betroffen:** `engine/adapters.py` (TED-Adapter).
- **Ursache:** Käufer aus Ausschreibungen werden als Kandidaten registriert.
- **Auswirkung:** 4 von 5 Auswahlen sind Behörden.
- **Korrektur:** Auftraggeber nicht als Anbieter-Kandidaten; Zuschlagsempfänger
  (Contract Award) statt Auftraggeber nutzen, falls verfügbar.

**P1-3 Keine automatische Website-Ermittlung.**
- **Betroffen:** `engine/adapters.py` (WebsiteAdapter), `sources/wikidata.py`.
- **Ursache:** Wikidata ist die einzige automatische Quelle (aus der Cloud gesperrt); eine
  Websuche ist bewusst ausgeschlossen.
- **Auswirkung:** 0 von 24 Firmen mit Website, keine Belege, keine Kennzahlen.
- **Korrektur:** kostenfreie, rechtlich saubere Website-Quelle bzw. -Heuristik (z. B. Impressum
  aus Registerdaten, Domainprüfung), Nutzerpflege in der UI. Auf einem Firmenrechner ist
  Wikidata eventuell erreichbar – **NICHT VERIFIZIERT**.

**P1-4 Lead-Liste zeigt veraltete Bewertungen.**
- **Betroffen:** `opportunity/leads.py`, `opportunity/assess.py:latest_assessments`; kein
  automatisches Neubewerten.
- **Auswirkung:** Avery „Fit 81, hoher Fit“ trotz behobener Logik; Widerspruch zur
  Opportunity-Seite.
- **Korrektur:** bei Modellwechsel neu bewerten (automatisch oder per Knopf), alte Bewertungen
  kennzeichnen statt anzeigen.

**P1-5 Kein Hintergrund-Worker.**
- **Betroffen:** `research.py`.
- **Auswirkung:** Recherche blockiert die Konsole; keine automatische Fortsetzung nach
  Neustart.
- **Korrektur:** Worker-Prozess bzw. -Thread mit Aufgabenverwaltung.

### P2 – Mittel

| Nr. | Problem | Ort / Auswirkung |
|---|---|---|
| P2-1 | Keine eigene Programmoberfläche | Konsolenfenster und Browser-Tab statt eigenem Fenster; kein Symbol (`.spec`) |
| P2-2 | Keine Einzelinstanz-Prüfung | `desktop.py`; zweiter Start öffnet zweiten Server |
| P2-3 | Doppelte Daten | 88 Signalgruppen doppelt; Pressemeldungen doppelt angezeigt (`intel/profile.py` bzw. `render.py`) |
| P2-4 | Wettbewerbsanalyse ohne Auswahl | ordnet alle Firmen ein (515 Zeilen) |
| P2-5 | Bericht zählt falsch und deutet falsch | Abrufe nur der letzten Sitzung; „Finde …“ als Marktführer-Rangliste gedeutet |
| P2-6 | Dubletten nicht bedienbar | Zusammenführen/Trennen fehlt |
| P2-7 | Keine Datensicherung | – |
| P2-8 | Unhandliche Bereitstellung | Setup nur als Actions-Artefakt (Anmeldung, 30 Tage); unsigniert |
| P2-9 | Keine Logdatei | Fehlerdetails gehen mit dem Konsolenfenster verloren |

### P3 – Niedrig

| Nr. | Problem |
|---|---|
| P3-1 | Interne Codes in der UI („unknown“, „company_reported“) |
| P3-2 | Startseite ohne Paginierung (516 Zeilen) |
| P3-3 | Doppelter Statustext je Lead-Zeile |
| P3-4 | Wachstumshinweise „Datum unbekannt“ trotz Pressedatum |
| P3-5 | Node-20-Warnung im Workflow (Actions-Versionen) |

---

## 14. Fehlende Funktionen

- Rechercheauftrag, Fortschritt, Abbruch und Bericht in der Oberfläche.
- Import von Firmenlisten und Dokumenten in der Oberfläche.
- Hintergrundverarbeitung und automatische Fortsetzung.
- Automatische Website-Ermittlung (kostenfrei).
- Zeitgesteuerte Aktualisierung.
- Benachrichtigungen.
- Dubletten zusammenführen/trennen.
- Automatische Wettbewerberidentifikation.
- Vergleichsansicht 2–10 Firmen in der UI.
- Neubewertung per UI.
- Datensicherung.
- Eigenes Programmfenster, Symbol, Einzelinstanz, Signatur.
- Dauerhafter Download (Release).

---

## 15. Empfohlene Zielarchitektur

**Weiterverwenden**, weil solide und getestet:
- Datenmodell und Repository,
- Abrufschicht (`http/gateway.py`),
- Quellenadapter GLEIF/SEC/ECB/Website,
- Extraktion mit Zitatprüfung,
- Zeitreihen,
- Bewertungslogik inklusive Rollen und Belegstatus,
- Lead-Export,
- Installer-Pipeline mit Windows-Test.

**Umbauen:**
1. **App-Hülle:** eigenes Fenster mit eingebetteter Weboberfläche (z. B. pywebview, kostenlos,
   BSD) statt Konsole und Browser; Einzelinstanz; Programmsymbol; Konsole ausblenden, Log in
   Datei.
2. **Worker:** Rechercheaufträge als Hintergrund-Thread bzw. -Prozess, gestartet von der
   Oberfläche; Fortschritt per Abfrage; automatische Wiederaufnahme beim Start.
3. **UI-Schicht:** Seiten für Auftrag, Fortschritt, Import, Upload, Neubewertung, Dubletten.
   Gestaltung nach den (noch bereitzustellenden) Referenzen.
4. **Discovery:** TED nur für Zuschlagsempfänger bzw. als Marktkontext; Website-Ermittlung als
   eigene Stufe mit Nutzerbestätigung.

---

## 16. Realistische Einschätzung des verbleibenden Aufwands

Keine Prozentzahl. Begründet nach Anforderungen:

| Bereich | Ausmaß | Nachweis |
|---|---|---|
| Sofort behebbar | **P0-1**: klein (eine Konfigurationsentscheidung plus ein Browsertest). Danach funktionieren alle vorhandenen Formulare. | §13 |
| Bedienbarkeit für Vertrieb | groß: P1-1, P1-4, P1-5, P2-1/2 – neue Seiten, Worker, App-Hülle | 28 Funktionen nur in der Kommandozeile |
| Ergebnisqualität der Automatik | groß und fachlich unsicher: P1-2, P1-3 – hängt an kostenfreien Quellen für Websites und Anbieter | echter Lauf: 0 von 24 Websites, 4 von 5 Behörden |
| Gestaltung nach Referenzen | nicht schätzbar, solange Referenzen fehlen | §6 |

---

## Ehrliches Gesamturteil

**Haben wir tatsächlich eine funktionierende Marktanalyse-Anwendung?**
Nein, nicht als Anwendung für Vertriebsnutzer. Es gibt einen funktionierenden Analyse- und
Bewertungskern, der per Kommandozeile bedient wird.

**Haben wir eine eigenständige Windows-App?**
Teilweise: echte EXE mit Installer und Deinstallation, auf Windows getestet. Die Oberfläche
läuft aber im externen Browser neben einem Konsolenfenster, ohne eigenes Fenster, Symbol oder
Einzelinstanz.

**Ist die Recherche wirklich automatisiert?**
Technisch ja: Aufträge laufen ohne Eingriff, über Abbruch und Neustart hinweg. Fachlich nein:
Der reale Lauf lieferte keine Websites, keine Kennzahlen und überwiegend Behörden.

**Können Nutzer echte Rechercheaufträge durchführen?**
Nur per Kommandozeile. In der installierten Oberfläche nicht.

**Werden Ergebnisse nachvollziehbar gespeichert?**
Ja: mit Quelle, Zitat, Abrufprotokoll, Historie und unveränderlichen Beobachtungen. Allerdings
mit Dubletten und mit veralteten Bewertungen in der Lead-Liste.

**Entspricht die UI den visuellen Referenzen?**
NICHT VERIFIZIERT – die Referenzen liegen dem Projekt nicht vor. Die vorhandene Oberfläche ist
schlicht und nicht auf Produktniveau.

**Welche Funktionen sind nur Attrappen oder Platzhalter?**
Keine im engeren Sinn – es gibt keine simulierten Ergebnisse. Wie Attrappen wirken aber:
- alle Speichern-Knöpfe (403 im echten Browser),
- die Lead-Liste (alte Bewertungen),
- die Wettbewerbsanalyse (ordnet jede Firma ein),
- der Hinweis „mi research“ auf der Startseite.

**Was müsste grundlegend neu aufgebaut werden?**
- die App-Hülle (Fenster statt Konsole und Browser),
- die Auftrags-, Fortschritts- und Import-Oberfläche,
- die Hintergrundverarbeitung,
- die Kandidatensuche (Anbieter statt Auftraggeber, Website-Ermittlung).

**Welche Komponenten sind gut genug zum Weiterverwenden?**
- Datenmodell und Persistenz,
- Abrufschicht mit Schutzmechanismen,
- Quellenadapter,
- Extraktion mit Zitatprüfung,
- Zeitreihen,
- Produkt-Fit- und Rollenlogik,
- Export,
- Installer-Pipeline,
- Sicherheitsmechanismen (nach Korrektur von P0-1).

**Kürzester sinnvoller Weg zu einem nutzbaren Produkt:**
1. P0-1 beheben und mit einem echten Browsertest absichern.
2. Neubewertung bei Modellwechsel (P1-4).
3. Auftragsseite mit Hintergrund-Worker und Fortschritt (P1-1, P1-5).
4. Import und Website-Pflege in der UI, TED-Auftraggeber ausschließen (P1-2, P1-3).
5. App-Hülle mit eigenem Fenster und Symbol (P2-1, P2-2).
6. Gestaltung nach den Referenzen, sobald sie vorliegen.

**Wie weit ist das Projekt vom vorgesehenen Produkt entfernt?**
Gemessen an den Anforderungen (§12):
- **BESTANDEN:** Datenlogik, Belege, Bewertung, Robustheit.
- **DEFEKT oder fehlend:** genau die Teile, die der Nutzer täglich braucht – Bedienung ohne
  Kommandozeile, speichernde Formulare, verlässliche automatische Kandidaten, Hintergrundbetrieb,
  eigene App-Hülle.

Der Abstand ist damit nicht in der Rechen- und Datenlogik, sondern in Produktoberfläche,
Bedienkonzept und Ergebnisqualität der automatischen Suche groß. Die Unzufriedenheit ist
anhand der Nachweise berechtigt. Teilweise zu korrigieren sind nur zwei Eindrücke:
- **„Setup öffnet eine BAT-Datei“:** Es ist eine echte EXE; was erscheint, ist ihr
  Konsolenfenster.
- **„große Teile funktionieren nicht“:** Für die Anzeige trifft das nicht zu. Für alle
  speichernden Aktionen, die Recherchebedienung und die Lead-Liste trifft es zu.
