# Sicherheit, Datenschutz und Rechtskonformität – Yellotools Market Intelligence

Stand: 2026-10-18 · Revision 2 (kostenloser, lokaler Betrieb)

> Technische Planung, **keine Rechtsberatung**. Punkte mit „Rechtsklärung“ müssen vor der
> jeweiligen Aktivierung durch Datenschutzbeauftragte bzw. Rechtsberatung bestätigt werden.

## 1. Schutzziele

1. Keine Beeinträchtigung des Auftragsimports (getrennte Anwendung, Daten, Zugangsdaten).
2. Rechtmäßige Datenerhebung: nur freigegebene Quellen; robots.txt, TDM-Vorbehalte und
   Nutzungsbedingungen werden eingehalten; keine Umgehung von Logins, Paywalls, CAPTCHAs,
   Sperren oder Nutzungsbeschränkungen.
3. Datensparsamkeit; **alle Daten bleiben auf eigener Hardware** (keine Cloud, keine
   KI-Dienste Dritter).
4. Integrität und Nachvollziehbarkeit (unveränderliche Beobachtungen, Abrufprotokoll).
5. Vertraulichkeit interner Bewertungen.
6. Keine Geheimnisse in Dateien.

## 2. Was verlässt den Rechner?

Nur die Anfragen an die freigegebenen Quellen: Suchbegriffe (z. B. Firmennamen,
„Fahrzeugfolierung“), LEI/QID/CIK, Webseiten-URLs, der User-Agent mit Kontaktadresse. Keine
Notizen, keine Bewertungen, keine Kundendaten, keine Dokumente. Die optionale KI läuft lokal;
der Code verweigert entfernte KI-Hosts (`llm/providers.py`, Test
`test_null_provider_and_remote_hosts`). Webseiten, PDFs und Dokumente sind nicht
vertrauenswürdige Daten: Sie werden als `<daten>` markiert, Begrenzer im Text entschärft,
KI-Ausgaben gegen ein Schema und wörtliche Zitate geprüft; Zahlen entstehen nie aus KI-Text
(Tests `test_llm_quotes_must_exist_in_text`, `test_untrusted_text_cannot_close_data_block`,
Prompt-Injection-PDF in `test_full_pipeline_all_steps_and_criteria`).

Hinweis Vertraulichkeit: Suchbegriffe verraten Quellenbetreibern das Interesse an bestimmten
Firmen/Themen. Das ist bei öffentlichen Registern üblich und vertretbar; sensible Analysen
sollten keine internen Projektnamen als Suchbegriffe enthalten.

## 3. Bedrohungsmodell

| # | Bedrohung | Maßnahme | Stand |
|---|---|---|---|
| T1 | SSRF über Links aus Webseiten/APIs | nur `https`, Port 443; DNS-Prüfung auf private/Loopback/Link-Local/Reserved; interne Namen gesperrt; keine Zugangsdaten in URLs; Weiterleitungen nicht blind gefolgt, nur gleiche Domain mit erneuter Prüfung | umgesetzt, Test `test_ssrf_protection`, `test_redirect_is_not_followed_blindly` |
| T1a | Hinter Firmen-Proxy keine lokale DNS-Auflösung | dann wird der Host nicht lokal geprüft; der Proxy muss interne Netze sperren (übliche `no_proxy`-Konfiguration) | dokumentiert |
| T2 | Bösartige/übergroße Antworten | Größenlimit (5 MB Standard), Zeitlimit 30 s, HTML-Parser der Standardbibliothek ohne Skriptausführung; keine PDF-Verarbeitung im Prototyp | umgesetzt (`test_response_size_limit`) |
| T3 | Prompt-Injection (nur bei aktivierter lokaler KI) | Modell ohne Werkzeuge/Netz; JSON-Schema-Ausgabe; Text als Daten markiert; jedes Ergebnis braucht wörtliches, geprüftes Zitat; Ergebnis kann höchstens einen Fit-Beleg erzeugen, nie eine Zahl | umgesetzt |
| T4 | XSS im HTML-Bericht | alle Werte `html.escape`; keine Skripte, keine externen Ressourcen im Bericht | umgesetzt |
| T5 | Formel-Injektion in CSV/XLSX | Zellen mit `= + - @ \t \r` werden mit `'` entschärft | umgesetzt (`test_formula_injection_is_neutralised`) |
| T6 | Geheimnisse in Dateien/Logs | Konfiguration lehnt Schlüssel wie `password`, `secret`, `token`, `api_key` ab; optionaler kostenloser Companies-House-Schlüssel nur per Umgebungsvariable | umgesetzt (`test_config_rejects_secrets`) |
| T7 | Lieferkette | exakt gepinnte Versionen, wenige Abhängigkeiten, Lizenzprüfung; Hash-Sperrdatei und `pip-audit` im CI | Hash-Sperrdatei und CI folgen in M0b |
| T8 | Datenmanipulation | append-only-Trigger für Beobachtungen und Belege, SHA-256 je Dokument | umgesetzt (SQLite); PostgreSQL-Trigger vorhanden, ungetestet |
| T9 | Überlast fremder Server | Drosselung je Quelle/Host, Anfragelimit je Run (Standard 2 000), Cache mit Revalidierung, eindeutiger User-Agent mit Kontakt | umgesetzt |
| T10 | Unbefugter Zugriff im Teambetrieb | Web-UI nur LAN/VPN, TLS, OIDC, Rollen, CSRF-Schutz, Sicherheits-Header | geplant (M6/M15) |

## 4. Datenschutz (DSGVO)

| Datenart | Umgang |
|---|---|
| Namen von Geschäftsführern/Ansprechpartnern | werden **nicht** extrahiert; das Datenmodell hat keine Personenfelder |
| Personenbezug in gespeicherten Rohseiten/Zitaten | Rohdokumente werden derzeit vollständig gespeichert (Nachvollziehbarkeit). **Offen (M13):** Aufbewahrungsfrist (Vorschlag 90 Tage Rohkopie, danach nur Belegzitate) und Bereinigung von E-Mail/Telefon in Zitaten |
| Einzelunternehmer (Firma = Name einer natürlichen Person) | geschäftliche Angaben, berechtigtes Interesse (Art. 6 Abs. 1 lit. f) – **Rechtsklärung** inkl. Art. 14 |
| Nutzerdaten | im Prototyp keine Benutzerkonten |

Da keine Cloud- oder KI-Dienste Dritter eingesetzt werden, entfällt für den Kern eine
Auftragsverarbeitung nach Art. 28. Löschung: Firmen können auf Anfrage gelöscht werden
(administrative Ausnahme vom append-only-Prinzip, Werkzeug folgt in M15).

## 5. Urheberrecht, Datenbankrecht, Nutzungsbedingungen

| Thema | Regel im System |
|---|---|
| Text und Data Mining (§ 44b UrhG) | robots.txt verbindlich; TDM-Vorbehalt per Header `tdm-reservation: 1` oder Meta-Tag → kein Speichern, keine Extraktion, Status `blocked_tdm`. Vorbehalte nur in AGB-Text erkennt die Software nicht automatisch – rechtlich umstritten (LG Hamburg/LAION); im Zweifel Domain über `disabled_sources` bzw. künftige Sperrliste ausschließen |
| Löschpflicht § 44b Abs. 2 | Rohkopien löschen, wenn nicht mehr erforderlich (Aufbewahrungsregel M13) |
| Datenbankherstellerrecht (§ 87b UrhG) | keine systematische Entnahme fremder Datenbanken ohne Erlaubnis; nur offene Lizenzen (CC0, gemeinfrei, DL-DE, Wiederverwendung mit Quellenangabe) |
| Nachrichten | nur Titel, URL, Datum (GDELT), keine Artikeltexte |
| Quellenangabe | Exporte enthalten je Wert Quelle, Herausgeber, URL, Abrufzeit; HTML-Bericht nennt Quellen |
| Zugriffsbeschränkungen | 401/403/429/CAPTCHA werden als Sperre respektiert; kein Login, keine Formulare, keine Proxy-Rotation, keine gefälschten Browser-Kennungen |
| Handelsregister | kein automatisierter Abruf |

## 6. Sicherheitstests (vorhanden)

`tests/test_gateway.py`: Freigabe, SSRF, robots, TDM, Cache, Retry-After, Netzfehler,
Größenlimit, Weiterleitungen, Anfragelimit. `tests/test_core.py`: append-only, Konflikte,
Zitatpflicht, Formel-Injektion, Geheimnisse in Konfiguration, KI nur lokal und nur mit
geprüftem Zitat. `tests/test_end_to_end.py`: Lauf ohne Internet erzeugt keine Zahlen,
dokumentiert Fehler.

Geplant: XSS-Korpus für die Web-UI, Prompt-Injection-Korpus für die lokale KI,
Autorisierungstests, `pip-audit` und Secret-Scan im CI.
