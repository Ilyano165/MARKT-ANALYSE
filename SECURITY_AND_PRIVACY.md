# Sicherheit, Datenschutz und Rechtskonformität – Yellotools Market Intelligence

Stand: 2026-10-18 · Phase 1

> Dieses Dokument ist eine technische Planung, **keine Rechtsberatung**. Punkte mit
> „Rechtsklärung“ müssen vor der jeweiligen Aktivierung durch Datenschutzbeauftragte bzw.
> Rechtsberatung von Yellotools bestätigt werden.

## 1. Schutzziele

1. Keine Beeinträchtigung des Auftragsimports (getrennte Anwendung, getrennte Daten, keine
   gemeinsamen Zugangsdaten).
2. Rechtmäßige Datenerhebung: nur freigegebene Quellen, Nutzungsbedingungen, robots.txt und
   TDM-Vorbehalte werden eingehalten; keine Umgehung technischer Schutzmaßnahmen.
3. Datensparsamkeit bei personenbezogenen Daten.
4. Integrität und Nachvollziehbarkeit der Daten (unveränderliche Beobachtungen, Audit-Log).
5. Vertraulichkeit der internen Bewertungen und Notizen (Vertriebsstrategie).
6. Schutz der Geheimnisse (API-Schlüssel, Datenbankzugang).

## 2. Bedrohungsmodell (Auszug)

| # | Bedrohung | Angriffsweg | Maßnahme |
|---|---|---|---|
| T1 | SSRF | manipulierte Links in Webseiten/Suchergebnissen führen zu internen Adressen | FetchGateway: DNS-Auflösung prüfen, private/Loopback/Link-Local/Metadaten-Adressen (z. B. 169.254.169.254) sperren, Weiterleitungen neu prüfen, nur Ports 443/80 |
| T2 | Bösartige Dokumente | PDF/HTML/XML mit Exploits, Zip-Bomben, riesige Dateien | Größen-/Seiten-/Zeitlimits, Parsing in separatem Prozess mit Ressourcenlimit, `defusedxml`, keine Ausführung aktiver Inhalte, kein JavaScript-Rendering in v1 |
| T3 | Prompt-Injection | Webinhalt enthält Anweisungen an das LLM | LLM ohne Werkzeuge und Netz, nur strukturierte Ausgabe, Dokument als Daten markiert, Zitat- und Plausibilitätsprüfung, keine Aktion auf Basis von LLM-Text |
| T4 | Stored XSS | Firmennamen, Zitate, Notizen enthalten HTML/JS | Autoescaping in Templates, strikte Content-Security-Policy ohne Inline-Skripte, keine externen Skripte/CDNs |
| T5 | Unbefugter Zugriff | offener Port, schwache Anmeldung | lokal nur 127.0.0.1 + Host-Prüfung; Team: TLS, OIDC, Rollen, Sitzungen mit kurzer Laufzeit, CSRF-Schutz |
| T6 | Geheimnisabfluss | Schlüssel in Logs/Exporten/DB | Geheimnisse nur im Anmeldespeicher/Secret-Store; Protokollbereinigung (Regeln aus Auftragsimport + API-Key-Muster); Exporte enthalten keine Konfiguration |
| T7 | Lieferkette | kompromittierte Pakete/Actions | Sperrdateien mit Hashes, `pip-audit` im CI, Actions auf Commit-SHA, minimale Abhängigkeiten, Dependabot/Renovate mit Review |
| T8 | Kostenmissbrauch | Endlosschleifen, große Runs | harte Budgets vor jedem Aufruf, Maximalwerte je Run, Admin-Freigabe für Runs über Schwellwert |
| T9 | Datenmanipulation | nachträgliche Änderung von Belegen | append-only + Trigger (PostgreSQL), Hash je Dokument, Audit-Log |
| T10 | Rechtsverstoß durch Crawling | Ignorieren von robots/AGB, Überlast fremder Server | Quellenfreigabe-Gate, robots.txt, TDM-Vorbehalt, konservative Rate Limits, identifizierender User-Agent mit Kontakt |

## 3. Datenschutz (DSGVO)

### 3.1 Welche personenbezogenen Daten können anfallen?

| Datenart | Vorkommen | Umgang |
|---|---|---|
| Namen von Geschäftsführern/Vorständen | Register, Impressum, Geschäftsberichte | **standardmäßig nicht gespeichert**; nur Funktion („Geschäftsführung gewechselt“) als Signal. Speicherung von Namen nur nach Freigabe als Option mit Rechtsgrundlage |
| Namen/Kontakte in Stellenanzeigen, Pressemitteilungen | Ansprechpartner, Zitierte | werden bei der Extraktion entfernt; Zitate werden vor dem Speichern auf E-Mail/Telefon geprüft und bereinigt |
| Einzelunternehmer (Firma = Name einer natürlichen Person, z. B. e.K.) | Register, Discovery | Firmendaten sind dann personenbezogen; Speicherung beschränkt auf geschäftliche Angaben, Rechtsgrundlage berechtigtes Interesse (Art. 6 Abs. 1 lit. f) – **Rechtsklärung**, inkl. Informationspflicht nach Art. 14 und deren Ausnahmen |
| Nutzerdaten der Anwendung | Login, Notizen, Audit-Log | Beschäftigtendatenschutz; Zweckbindung, Aufbewahrungsfristen festlegen |
| Rohdokumente (Webseiten, PDFs) | können Personenbezug enthalten | Aufbewahrung nach `retention_policy`, Standard: Rohkopie 90 Tage, danach nur Text-Auszüge der verwendeten Belege |

### 3.2 Maßnahmen

- Datensparsamkeit als Standard; Felder für Personen existieren im Datenmodell nicht.
- Automatische Bereinigung von E-Mail-Adressen und Telefonnummern in Zitaten und Protokollen.
- Verzeichnis von Verarbeitungstätigkeiten: Entwurf wird mit M15 geliefert.
- Prüfung der Erforderlichkeit einer Datenschutz-Folgenabschätzung (Art. 35) durch den DSB.
- Auftragsverarbeitung (Art. 28) bzw. Prüfung der Übermittlung für Cloud-Dienste:
  LLM-Anbieter, Such-API, gehostete Datenbank. An das LLM gehen nur öffentliche
  Unternehmensdokumente, keine internen Notizen und keine Kundenlisten.
- Löschkonzept: Firmen können auf Anfrage vollständig gelöscht werden (Ausnahme vom
  append-only-Prinzip, protokolliert ohne Inhalt).
- Rechte Betroffener (Auskunft/Löschung) über Admin-Funktion „alle Daten zu Firma X“.

## 4. Urheberrecht, Datenbankrecht, Nutzungsbedingungen

| Thema | Regel im System |
|---|---|
| Text und Data Mining (§ 44b UrhG) | Vervielfältigung für TDM ist zulässig, sofern kein maschinenlesbarer Nutzungsvorbehalt besteht. Das Gateway prüft robots.txt (inkl. Regeln für den eigenen User-Agent) und bekannte TDM-Vorbehalt-Signale (z. B. `tdm-reservation`-Meta/Header nach TDMRep). Ist ein Vorbehalt erkannt, wird das Dokument **nicht** gespeichert und nicht extrahiert; ein Treffer wird als `data_gap(reason=legal_blocked)` protokolliert. Ob Vorbehalte in natürlicher Sprache (AGB/Impressum) genügen, ist rechtlich umstritten (LG Hamburg, LAION) – im Zweifel gilt: Quelle sperren. |
| Löschpflicht § 44b Abs. 2 | Rohkopien werden gelöscht, sobald sie für die Analyse nicht mehr erforderlich sind (Standard 90 Tage) |
| Datenbankherstellerrecht (§ 87b UrhG) | Keine systematische Entnahme wesentlicher Teile fremder Datenbanken (Verzeichnisse, Datenanbieter) ohne Lizenz |
| Zitate | Belegzitate kurz (≤ 1 000 Zeichen), Zusammenfassungen von Signalen in eigenen Worten; Exporte enthalten Zitate nur als Beleg, keine Volltexte |
| Nutzungsbedingungen | je Quelle im Register dokumentiert (`terms_url`, `terms_checked_at`); Änderungen werden halbjährlich geprüft (Schedule) |
| Zugriffsbeschränkungen | Kein Login-Scraping, keine Paywall-, CAPTCHA- oder IP-Sperren-Umgehung, keine rotierenden Proxys, keine gefälschten Browser-Kennungen. HTTP 401/403/429 und CAPTCHA-Seiten werden als Sperre respektiert |
| Handelsregister | kein automatisierter Abruf (siehe `RESEARCH_SOURCES.md`) |
| Such-API-Ergebnisse | nur URL/Titel als Lead speichern, bis die Speicherrechte vertraglich geklärt sind |

## 5. Geheimnisse und Konfiguration

- API-Schlüssel: Einzelplatz im Windows-Anmeldespeicher (`keyring`, Muster aus
  `icware_auftragsimport/security/credentials.py`); Team: Secret der Laufzeitumgebung (z. B.
  Docker Secrets), nie in Images.
- Konfigurationsdateien werden beim Laden auf Schlüssel wie `password`, `secret`, `token`,
  `key` geprüft und abgelehnt (Muster aus `config/policy.py`).
- Rotation: Schlüssel je Quelle austauschbar ohne Neustart der Oberfläche.

## 6. Zugriffskonzept (Teambetrieb)

| Rolle | Rechte |
|---|---|
| `viewer` | lesen, exportieren (Exporte protokolliert), private Notizen |
| `analyst` | zusätzlich Runs anlegen/steuern bis Budget-Schwelle, Prüfliste entscheiden, Team-Notizen, Watchlists |
| `admin` | zusätzlich Quellen freigeben, Budgets, Scoring-Modelle aktivieren, Benutzer, Löschungen |

Alle schreibenden Aktionen → `audit_log` (wer, wann, was, vorher/nachher-Verweis).

## 7. Betriebssicherheit

- TLS über Reverse Proxy; HSTS; sichere Cookies (`Secure`, `HttpOnly`, `SameSite=Strict`).
- Sicherheits-Header: CSP, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors 'none'`.
- Tägliche Datenbanksicherung, verschlüsselt; monatlicher Wiederherstellungstest.
- Protokolle bereinigt, Rotation, Aufbewahrung 90 Tage.
- Container laufen ohne Root, schreibgeschütztes Dateisystem außer Datenverzeichnis.

## 8. Sicherheitstests (in der Roadmap verankert)

- SSRF-Testsuite gegen das Gateway (private IPs, DNS-Rebinding, Weiterleitungen, IPv6, dezimale IP-Schreibweisen).
- Bösartige Dokumente (übergroß, verschachtelt, XML-Entitäten, defekte PDFs) – nach Vorbild
  `tests/security` im Auftragsimport.
- Prompt-Injection-Korpus für den LLM-Extraktor: Dokumente mit eingebetteten Anweisungen;
  Abnahme: keine Beobachtung ohne verifiziertes Zitat, keine Abweichung vom Schema.
- XSS-Korpus für alle Ausgabefelder.
- Autorisierungstests je Route und Rolle.
- `pip-audit`, `ruff` Sicherheitsregeln, Secret-Scan im CI.
