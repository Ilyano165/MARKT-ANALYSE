# Produktspezifikation – Yellotools Market Intelligence

Stand: 2026-10-20 · Revision 3 (Phase 3 Company Intelligence; verbindlich: 100 % kostenloser Betrieb) · Status: Entwurf zur Freigabe

## 1. Zweck

Ein dauerhaft nutzbares Recherche- und Analysewerkzeug, mit dem Yellotools

- Marktführer und Wettbewerber in ausgewählten Branchen und Ländern identifiziert und vergleicht,
- neue Absatzmärkte und Kundengruppen – auch außerhalb der Werbetechnik – findet,
- potenzielle Kunden mit nachvollziehbarer Begründung priorisiert,
- die Entwicklung relevanter Unternehmen über mehrere Jahre verfolgt.

Jede Aussage im System ist auf eine konkrete Quelle zurückführbar. Fehlende Daten bleiben
sichtbar unbekannt.

## 2. Nicht-Ziele

- Keine verpflichtenden Kosten: keine kostenpflichtigen Such-APIs, Datenbanken, Datenanbieter,
  KI-Dienste, Abonnements, Cloud- oder Hostingdienste (Details: `docs/DEPENDENCIES_AND_COSTS.md`).
- Kein KI-Chat und keine frei formulierten KI-Antworten ohne Belege; KI ist optional und lokal.
- Kein CRM (keine Kontaktpflege, keine Ansprechpartner-Datenbank, kein E-Mail-Versand). Export
  in das CRM ist möglich.
- Keine Personenrecherche; keine Sammlung von Kontaktdaten natürlicher Personen.
- Kein Umgehen von Logins, Paywalls, CAPTCHAs oder technischen Sperren; kein Scraping von
  Plattformen, deren Bedingungen es untersagen.
- Keine Änderungen am Auftragsimport; keine gemeinsame Datenbank mit ihm.

## 3. Nutzergruppen

| Rolle | Ziele | Typische Aufgaben |
|---|---|---|
| Geschäftsführung | Marktpotenzial, strategische Märkte | Marktübersichten, Vergleiche, Exporte für Entscheidungen |
| Vertriebsleitung / Export | Zielmärkte und Zielkunden priorisieren | Customer Discovery, Chancenlisten, Watchlists |
| Außendienst / Key Account | Vorbereitung auf Kunden und Interessenten | Firmenprofil, Signale, Notizen |
| Analyst (Power-User) | Recherchen anlegen, Daten prüfen | Runs steuern, Prüfliste, Quellen bewerten, Scoring pflegen |
| Administrator | Betrieb, Quellen, Budgets, Benutzer | Quellenfreigabe, Kostenlimits, Rollen, Backups |

## 4. Fachliche Module und Anforderungen

Anforderungs-IDs werden in Roadmap und Tests referenziert. Priorität: **M** = Muss (v1),
**S** = Soll, **K** = Kann.

### A. Market Intelligence

| ID | Anforderung | Prio |
|---|---|---|
| FR-A-01 | Recherche „Marktführer“ für Branche (NACE/WZ oder eigene Kategorie) × Land(er) anlegen, mit Budget und maximaler Firmenzahl | M |
| FR-A-02 | Ergebnisliste mit Umsatz, Mitarbeitern, Sitz, Konzernzugehörigkeit – jeweils mit Zeitraum, Status und Quelle; unbekannte Werte als „unbekannt (gesucht in: …)“ | M |
| FR-A-03 | Ranking nur auf belegten Werten; Firmen ohne Werte erscheinen getrennt („nicht bewertbar“) statt geschätzt einsortiert | M |
| FR-A-04 | Standorte und geografische Präsenz (Länder, Art der Präsenz) | M |
| FR-A-05 | Internationale Vertriebsmärkte nur mit Beleg, Belegstärke sichtbar (Tochter > Händler > Sprachversion der Webseite) | S |
| FR-A-06 | Umsatz- und Beschäftigungsentwicklung als Zeitreihe über mehrere Jahre mit Kennzeichnung von Brüchen (GJ-Wechsel, Konsolidierungskreis, Methodik) | M |
| FR-A-07 | Vergleich von 2–10 Unternehmen oder Unternehmensgruppen nebeneinander (Kennzahlen, Wachstum, Präsenz) | M |
| FR-A-08 | Konzernsicht: Tochtergesellschaften gruppieren, Doppelzählung bei Summen vermeiden (nur konsolidierte Werte der Mutter, falls vorhanden) | S |
| FR-A-09 | Marktgröße je Branche/Land aus amtlicher Statistik (Unternehmen, Beschäftigte, Umsatz) als Kontext | S |

### B. Customer Discovery

| ID | Anforderung | Prio |
|---|---|---|
| FR-B-01 | Produktkategorien aus Yellotools-Artikelstamm importieren (CSV/XLSX), Pflege und Freigabe durch Vertrieb | M |
| FR-B-02 | Anwendungsfälle pflegen und mit Produktkategorien und Branchen verknüpfen; Status Hypothese/validiert | M |
| FR-B-03 | Discovery-Recherche: Produktkategorie oder Anwendungsfall × Land → Kandidatenliste | M |
| FR-B-04 | Je Kandidat Produktfit (stark/plausibel/schwach/ausgeschlossen) mit Begründung und Belegen; ohne Beleg kein „stark“/„plausibel“ | M |
| FR-B-05 | Wachstumsindikatoren erkennen: neue Standorte, Investitionen, Stellenzuwachs, Ausschreibungsgewinne, Flottenausbau | S |
| FR-B-06 | Priorisierung A/B/C mit nachvollziehbarer Punktzerlegung und Angabe der Datenvollständigkeit | M |
| FR-B-07 | Bestehende Yellotools-Kunden (Import einer Firmenliste) markieren und aus Neukundenlisten ausblenden bzw. als Referenz für das Scoring nutzen | S |
| FR-B-08 | Kandidat verwerfen mit Grund; verworfene Kandidaten erscheinen in späteren Runs nicht erneut als neu | M |

### C. Company Intelligence

| ID | Anforderung | Prio |
|---|---|---|
| FR-C-01 | Einheitliches Firmenprofil: Anzeigename, rechtlicher Name, Rechtsform, Sitz, Identifikatoren, Status | M |
| FR-C-02 | Mutter-/Tochterbeziehungen mit Art (Konsolidierung/Eigentum/Niederlassung) und Quelle | M |
| FR-C-03 | Branche(n) und Unterbranchen mit Klassifikationsschema | M |
| FR-C-04 | Länder, Standorte, Niederlassungen | M |
| FR-C-05 | Umsatzhistorie und Beschäftigungshistorie – alle Beobachtungen, nicht nur der gültige Wert | M |
| FR-C-06 | Zu jeder Angabe: Quelle (URL, Titel, Herausgeber), Veröffentlichungs- und Abrufdatum, Zeitraum, Herkunftsklasse, Status, Vertrauen, Methodik, Zitat | M |
| FR-C-07 | Veränderungen zwischen Analysezeiträumen (Änderungsprotokoll je Firma) | M |
| FR-C-08 | Aliase, frühere Namen, Marken | M |
| FR-C-09 | Notizen (privat/Team) | S |
| FR-C-10 | Zusammenführen und Trennen von Firmen über die Prüfliste, mit Protokoll | M |
| FR-C-11 | Profil mit Marken, Produktgruppen/Leistungen, Vertriebswegen, Zielgruppen, Absatzländern und Wachstumshinweisen – jede Angabe mit Beleg (Phase 3, umgesetzt) | M |
| FR-C-12 | Umsatzentwicklung 5–10 Geschäftsjahre: Δ absolut, Δ %, Gesamtveränderung, CAGR mit Formel; Brüche bei Währung, Geschäftsjahr, Konsolidierungskreis; Übernahmen kennzeichnen (umgesetzt, Regeln §7a) | M |
| FR-C-13 | Mitarbeiterentwicklung je Jahr, Nettoveränderung getrennt von belegten Einstellungen/Abbau und Stellenanzeigen; nach Land/Standort, wenn belegt (umgesetzt) | M |
| FR-C-14 | Standorte/Märkte in drei Stufen: physisch – beliefert/Export – nur erwähnt; Karte nur mit ausreichend genauen Koordinaten (umgesetzt) | M |
| FR-C-15 | Wettbewerbsanalyse direkt/indirekt/verwandter Markt mit Begründung je Dimension, ohne Marktanteile (umgesetzt) | M |
| FR-C-16 | Änderungserkennung bei erneuter Recherche mit Beleg, Ereignisdatum und Erfassungsdatum (umgesetzt) | M |

### D. Research Engine

| ID | Anforderung | Prio |
|---|---|---|
| FR-D-01 | Automatisierte Recherche über austauschbare Quellenadapter (siehe `RESEARCH_SOURCES.md`) | M |
| FR-D-02 | Runs anlegen, pausieren, fortsetzen, abbrechen; Fortschritt, Kosten, Fehler und Lücken live sichtbar | M |
| FR-D-03 | Runs laufen im Hintergrund; Oberfläche bleibt bedienbar; Neustart des Servers setzt Runs fort | M |
| FR-D-04 | Anfragelimit je Run und Drosselung je Quelle (freie Quellen haben Abruflimits); Erreichen wird gemeldet. Es gibt keine kostenpflichtigen Abrufe | M |
| FR-D-05 | Rate Limits, Wiederholungen, Cache, Duplikatschutz zentral | M |
| FR-D-06 | Ausfall einer Quelle bricht den Run nicht ab; Ergebnis `completed_with_gaps` | M |
| FR-D-07 | Dokument-Upload durch Nutzer (z. B. Jahresabschluss-PDF, Verbandsliste) als Quelle mit Herkunft „manuell bereitgestellt“ | M |
| FR-D-08 | Extraktion aus PDF/HTML regelbasiert; optional **lokale** KI (z. B. Ollama) mit Zitatprüfung; alles funktioniert ohne KI | S |
| FR-D-09 | Quellenfreigabe: nur freigegebene Quellen werden abgerufen | M |
| FR-D-10 | Zeitgesteuerte Aktualisierung von Watchlists | S |

### E. Analytics

| ID | Anforderung | Prio |
|---|---|---|
| FR-E-01 | Zeitreihen je Kennzahl mit Wachstumsraten (YoY, CAGR) nur zwischen vergleichbaren Werten (gleicher Scope, gleiche Einheit, Zeitraumlänge) | M |
| FR-E-02 | Unternehmensvergleich (siehe FR-A-07) | M |
| FR-E-03 | Änderungserkennung mit Schwellwerten und Benachrichtigung in der App | S |
| FR-E-04 | Chancenindikatoren mit Zerlegung (siehe FR-B-06) | M |
| FR-E-05 | Datenlücken- und Unsicherheitsübersicht je Liste (wie viele Werte bestätigt/geschätzt/widersprüchlich/unbekannt) | M |
| FR-E-06 | Export CSV, XLSX, JSON – jede Zeile mit Quelle, Zeitraum, Status; Exportprotokoll | M |
| FR-E-07 | Währungsumrechnung als abgeleiteter Wert mit Kursdatum, Original immer sichtbar | M |

## 5. Bildschirme (v1)

1. **Start**: laufende Runs, neue Änderungen in Watchlists, offene Prüfeinträge, Kostenstand.
2. **Recherche anlegen**: Typ, Parameter, Budget, Vorschau der verwendeten Quellen und
   geschätzten Kosten.
3. **Run-Detail**: Fortschritt je Aufgabe, Kosten, Fehler, Lücken; Pause/Fortsetzen/Abbrechen.
4. **Firmenliste / Marktübersicht**: Filter (Land, Branche, Größe, Status, Fit, Priorität),
   Spalten mit Statussymbolen (bestätigt, gemeldet, geschätzt, widersprüchlich, unbekannt).
5. **Firmenprofil**: Stammdaten, Identifikatoren, Struktur, Standorte/Karte, Kennzahlen mit
   Zeitreihen, Signale, Produktfit, Chancenbewertung, Notizen, Änderungen, Quellen.
6. **Kennzahl-Detail** (Klick auf Wert): alle Beobachtungen, Quelle, Zitat, Methodik, Konflikte.
7. **Vergleich**: 2–10 Firmen.
8. **Chancenliste**: priorisiert, mit Punktzerlegung.
9. **Prüfliste**: Konflikte, Merge-Kandidaten, unsichere Extraktionen – mit Entscheidung und Begründung.
10. **Stammdaten**: Produktkategorien, Anwendungsfälle, Scoring-Modelle.
11. **Administration**: Quellenregister und Freigaben, Budgets, Benutzer, Systemzustand.

Darstellung von Unsicherheit ist Pflicht: Werte ohne Status werden nie angezeigt; geschätzte
Werte sind kursiv mit „geschätzt“ gekennzeichnet; Widersprüche haben ein Warnsymbol.

**Umgesetzt in Phase 3 (Version 0.3.0, `mi ui`, lokal):** Firmenliste mit Suche (4, ohne
Filter), Firmenprofil (5) mit den Abschnitten Überblick, Finanzen, Mitarbeiter, Standorte,
Produkte, Märkte, Historie, Quellen, interne Notizen; interaktive Diagramme für Umsatz und
Mitarbeiter (Werte / Δ % / Δ absolut, Tooltip mit Herkunft, Lücken als gestrichelter Rahmen
„unbekannt“, Brüche als Linie); Karte (Länder nach Präsenzstufe, Punkte nur mit Orts- oder
Adressgenauigkeit); Kennzahl-Detail (6) als Herkunftsseite; Wettbewerbsanalyse. Ein
eigenständiger HTML-Export (`mi profile --html`) enthält dieselben Abschnitte ohne Server.
Offen: Bildschirme 1–3 und 7–11.

## 6. Chancenbewertung (Scoring v1, Startentwurf)

Teilindikatoren, je 0–1 normiert, Gewichte durch Vertrieb freizugeben:

| Indikator | Datengrundlage | Startgewicht |
|---|---|---|
| Produktfit | `company_product_fit` (stark=1, plausibel=0,6, schwach=0,2) | 0,30 |
| Unternehmensgröße | Mitarbeiter/Umsatz (log-normiert je Branche) | 0,15 |
| Wachstum | Mitarbeiter-/Umsatz-CAGR (nur vergleichbare Werte) | 0,15 |
| Aktuelle Signale | Investitionen, neue Standorte, Stellen mit Anwendungsbezug (letzte 12 Monate, zeitlich abklingend) | 0,20 |
| Marktzugang | Präsenz in Zielland, Nähe zu bestehenden Yellotools-Märkten | 0,10 |
| Ähnlichkeit zu Bestandskunden | Branche/Größe/Anwendungsfall | 0,10 |

Fehlende Indikatoren werden **nicht** mit 0 und nicht mit einem Schätzwert gefüllt; der Score
wird über die vorhandenen Gewichte renormiert und `data_completeness_pct` ausgewiesen. Unter
50 % Vollständigkeit lautet die Priorität `insufficient_data`.

## 7. Nicht-funktionale Anforderungen

| ID | Anforderung |
|---|---|
| NFR-01 | Seitenaufbau Firmenprofil < 1 s bei 50 000 Firmen / 2 Mio. Beobachtungen (PostgreSQL) |
| NFR-02 | Kein UI-Request wartet auf externe Quellen |
| NFR-03 | Neustart/Absturz verliert keine abgeschlossene Aufgabe; laufende Aufgaben werden wiederholt (idempotent) |
| NFR-04 | Betrieb ohne jede verpflichtende Kostenposition; Anfragelimits werden vor dem Aufruf geprüft |
| NFR-05 | Nachvollziehbarkeit: zu jedem angezeigten Wert in ≤ 2 Klicks Quelle und Zitat |
| NFR-06 | Testabdeckung Domain/Application ≥ 90 % Zweige; jede Quelle mit Vertragstests |
| NFR-07 | `mypy --strict`, `ruff` ohne Befunde; Abhängigkeiten gepinnt mit Prüfsummen, `pip-audit` ohne bekannte Schwachstellen |
| NFR-08 | Oberfläche Deutsch (Englisch vorbereitet), barrierearm (Tastatur, Kontraste) |
| NFR-09 | Datensicherung täglich, Wiederherstellung dokumentiert und getestet |
| NFR-10 | Datenschutz und Urheberrecht gemäß `SECURITY_AND_PRIVACY.md` |

## 7a. Rechen- und Darstellungsregeln der Unternehmensanalyse (Phase 3, verbindlich)

Umgesetzt in `src/market_intel/intel/timeseries.py`, geprüft in `tests/test_timeseries.py` und
`tests/test_intel.py`.

1. **Lücken bleiben Lücken.** Ein fehlendes Jahr wird als „unbekannt“ angezeigt – nie als 0,
   nie interpoliert, nie als Stillstand. Über eine Lücke gibt es keine Vorjahresveränderung,
   nur eine ausdrücklich gekennzeichnete Mehrjahresveränderung („seit 2019: +15 %“).
2. **Vorjahresveränderung** Δ absolut = Wert(t) − Wert(t−1); Δ % = (Wert(t) / Wert(t−1) − 1) × 100,
   nur zwischen vergleichbaren Werten und nur bei Vorjahreswert > 0.
3. **Gesamtveränderung und CAGR** je vergleichbarem Abschnitt:
   CAGR = (Endwert / Anfangswert)^(1 / Jahre) − 1. Voraussetzung: mindestens ein Jahr Abstand,
   beide Werte > 0. Die Formel mit eingesetzten Werten wird angezeigt. Fehlen Zwischenjahre,
   steht dabei: „CAGR beruht nur auf den Endpunkten“.
4. **Vergleichbar** sind Werte nur bei gleicher Einheit/Währung, gleichem bekannten
   Konsolidierungskreis (Konzern ↔ Einzelgesellschaft = Bruch; „unbekannt“ = Hinweis), gleichem
   Geschäftsjahresende und regulärer Periodenlänge (335–396 Tage). Rumpf- und verlängerte
   Geschäftsjahre sind Brüche. Jeder Bruch beginnt einen neuen Abschnitt.
5. **Währungen:** Analyse in Originalwährung. Eine EUR-Umrechnung (EZB-Jahresdurchschnitt) wird
   als eigene Reihe gezeigt und trägt den Hinweis „enthält Wechselkurseffekte“; eine
   währungsbereinigte Rate wird nicht berechnet.
6. **Übernahmen, Verkäufe, Fusionen** im Zeitraum (bestätigt oder gemeldet, Vertrauen
   mittel/hoch) machen die Veränderung „nicht organisch“ – sie wird gezeigt, aber gekennzeichnet.
   Änderung des Konsolidierungskreises oder Geschäftsjahres ist ein Bruch. Unbestätigte
   Regel-Treffer aus Pressetexten erscheinen nur als „möglicher Einfluss“.
7. **Schätzungen** gehen nur auf ausdrücklichen Wunsch ein, brauchen Methodik, Quelle und
   Unsicherheit und sind in Tabelle und Diagramm markiert.
8. **Mitarbeiter:** Stichtag, Jahresdurchschnitt (§ 267 Abs. 5 HGB: Mittel der vier
   Quartalsstichtage, ohne Auszubildende) und Vollzeitäquivalente sind getrennte Reihen.
   Nettoveränderung ≠ Einstellungen/Abgänge (1.000 → 1.100 heißt nicht 100 Einstellungen).
   Belegte Abbau-/Einstellungsmeldungen und Stellenanzeigen (= Bedarf) stehen daneben.
9. **Jede angezeigte Zahl** nennt Jahr bzw. Stichtag, Konzern/Einzelgesellschaft/unbekannt,
   Herkunft (amtlich, Unternehmensangabe, Drittquelle, abgeleitet, Schätzung) und [Qn]; über
   „Beleg“ führt sie zu Beobachtung, Zitat, Fundstelle, Dokument (SHA-256) und Abrufprotokoll.
10. **Wettbewerb:** keine Marktanteile. Größenvergleiche sind Verhältnisse zweier belegter Werte
    mit Hinweis auf abweichende Jahre, Konsolidierungskreise oder Umrechnungen.
11. **Änderungen** bei erneuter Recherche: Ereignisdatum (laut Quelle) und Erfassungsdatum
    (System) getrennt. Aus dem Fehlen einer Angabe wird nichts abgeleitet (keine „Schließung“).

## 8. Qualitätsgrundsätze

1. Erfinde nichts. Unbekannt bleibt unbekannt.
2. Jeder Wert hat Quelle, Zeitraum, Erfassungszeitpunkt, Herkunftsklasse und Status.
3. Neue Daten überschreiben alte nicht; Änderungen werden sichtbar gemacht.
4. Widersprüche werden gezeigt, nicht geglättet.
5. Automatisierung schlägt vor, Menschen entscheiden bei Unsicherheit.
6. Rechtmäßige Quellen only; im Zweifel gesperrt bis zur Klärung.

## 9. Glossar

| Begriff | Bedeutung |
|---|---|
| Beobachtung | eine einzelne, belegte Angabe zu einer Kennzahl für einen Zeitraum aus einer Quelle |
| Gültiger Wert | nach Regeln abgeleiteter Anzeigewert aus allen Beobachtungen (`DATA_MODEL.md` §6) |
| Herkunftsklasse | amtlich / Unternehmensangabe / Dritter / abgeleitet / Schätzung |
| Datenlücke | gezielt gesucht, nicht gefunden – mit Liste der geprüften Quellen |
| Run | ein Rechercheauftrag, bestehend aus Aufgaben |
| Produktfit | belegte Einschätzung, ob ein Unternehmen einen Yellotools-Anwendungsfall hat |
| Signal | Ereignis wie Investition, neuer Standort, Ausschreibung |
