# KI-Architektur: KI-Manager, NOVA als Standard, externe Provider

Stand: 2026-11-12, Version 0.8.0 (Phase H). Verbindlicher Architekturauftrag: Desktop-App,
austauschbare KI, NOVA-Fallback.

## 1. Untersuchung des Bestands (Schritt A)

### MARKT-ANALYSE vor Phase H

- **Desktop:** Seit Phase E ist MARKT-ANALYSE eine eigenständige Windows-Anwendung.
  - pywebview betten Edge WebView2 in ein natives Fenster ein (`MarketIntel.exe`, kein
    Konsolenfenster).
  - Es gibt einen NSIS-Installer mit Startmenü- und Desktop-Verknüpfung und eine
    Einzelinstanz.
  - Ein externer Browser wird nicht gebraucht. Ohne WebView2 gibt es den
    Edge-App-Modus als Rückfall.
  - Das Fenster prüft der Windows-CI-Lauf in jedem Durchgang („Fenstertest“).
- **KI:** Es gab nur `llm/providers.py`, also Ollama und lokale OpenAI-kompatible Server.
  - Die KI war standardmäßig aus.
  - Cloud-Anbieter waren bewusst ausgeschlossen.
  - Alle KI-Aufgaben liefen über `llm/assistant.py`. Dort wird jede Antwort
    deterministisch geprüft: Zitate müssen wörtlich vorkommen, Faktreferenzen müssen
    stimmen, Zahlen müssen belegt sein.
  - Die Recherche, also die Beschaffung von Quellen, war schon getrennt (`research.py`,
    `sources/`, `http/gateway.py`).

### NOVA (Repository `Ilyano165/MY-LOCAL-AI`, Version 0.2.2)

| Thema | Befund (aus dem NOVA-Quellcode und `docs/integration-api.md`, `docs/install-windows.md`) |
|---|---|
| Schnittstelle | Integrations-API `http://127.0.0.1:8765/api/v1` (NOVA-Format), dazu OpenAI-kompatibel `/v1` |
| Authentifizierung | Integrationsschlüssel `nova_<12 hex>_<Geheimnis>` als `Authorization: Bearer`. Er wird mit `nova integrations create --name … --scope chat:complete --scope models:read` angelegt und **einmal** angezeigt; NOVA speichert nur den Hash. |
| Endpunkte | `GET /health` (öffentlich), `GET /models`, `POST /chat/completions` (Modell `nova-auto` = NOVA-Router) |
| Fehler | Fehlerobjekt `{"error":{"code",…}}`. Codes: 401 `invalid_api_key`, 403 `insufficient_scope`, 429 `rate_limited`, 503 `no_model` (Setup-Modus), 504 `timeout` |
| Grenzen | **Kein JSON-Modus**, kein Function Calling, `response_format` wird abgelehnt. Nur Loopback-Clients. |
| Start | Startmenü „NOVA“ (`nova-desktop.exe`) oder `nova service start`. Programme liegen unter `%LOCALAPPDATA%\Programs\NOVA\nova.exe`, Daten unter `%LOCALAPPDATA%\NOVA`. |
| Modell | Nicht im Installer. Die Ein-Klick-Einrichtung lädt llama.cpp und ein Qwen3.5-Modell; NOVA startet `llama-server` selbst. |

**Gefundener Konflikt:** Die MARKT-ANALYSE-Oberfläche nutzte standardmäßig ebenfalls Port
**8765**. Startete MARKT-ANALYSE zuerst, konnte NOVA nicht starten: NOVA meldet dann einen
Port-Konflikt, und das Startmenü nutzt fest 8765. Behoben: MARKT-ANALYSE nutzt jetzt 8780
(siehe `desktop.py`, `mi ui`).

## 2. Architektur (Schritt B)

```
Recherche (research.py, sources/, gateway)      ← beschafft Quellen, speichert Dokumente
        │  nur bereits abgerufene Texte
        ▼
Assistant (llm/assistant.py)                    ← Aufgaben + deterministische Prüfung jeder Antwort
        │  complete_task(art, aufgabe, text, schema)
        ▼
KI-Manager (llm/manager.py)                     ← Auswahl, Fallback-Regel, Protokoll ai_call, Status
        │
        ├── NovaProvider      → NOVA /api/v1 (Standard)
        ├── AnthropicProvider → offizielles SDK, strukturierte JSON-Ausgabe
        ├── OpenAIProvider    → /v1/chat/completions mit json_schema
        └── lokaler Server    → Ollama / OpenAI-kompatibel aus market_intel.toml [llm]
Schlüssel: llm/secrets.py → Windows-Anmeldeinformationsverwaltung (nie DB, nie Datei, nie Log)
```

- **Einheitliches Format:** Jeder Provider liefert `AIReply(data, provider, model,
  request_id, seconds)` oder wirft `ProviderError(kind)`. Fehlerarten:
  - `auth`, `permission`, `unreachable`, `timeout`, `rate_limit`, `unavailable`, `server`
  - `bad_request`, `refused`, `invalid_output`, `not_configured`, `limit`
- **Auswahl** (`manager.resolve`):
  1. Ein ausdrücklich gewählter externer Provider mit hinterlegtem Key.
  2. Sonst NOVA, auch wenn ein externer Provider gewählt ist, aber kein Key hinterlegt ist.
     Der Grund wird angezeigt.
  3. Ist der Provider nicht nutzbar, erscheint eine verständliche Meldung. Die KI-Aufgabe
     findet dann nicht statt, und die Recherche läuft regelbasiert weiter.
- **Fallback-Regel:** Standard ist `keiner`.
  - `nova_bei_ausfall` wechselt nur bei Ausfall des externen Anbieters zu NOVA (nicht
    erreichbar, Zeitüberschreitung, Überlastung, Limit).
  - Bei ungültigem Key wird **nie** gewechselt.
  - Jeder Wechsel steht in `ai_call.fallback_from` und erscheint in Statusleiste,
    KI-Einstellungen und Auftrag.
- **Blockieren:** Nach `auth`, `permission`, `not_configured`, `unavailable` oder „NOVA
  nicht erreichbar“ wird der Provider im laufenden Auftrag nicht erneut gefragt. So gibt es
  keine Fehlerflut, aber einen klaren Eintrag.
- **Kennzeichnung:** KI-Belege tragen `extraction_method = llm:<provider>/<modell>`, zum
  Beispiel `llm:nova/local-general`, und bleiben an `source_document_id` und das wörtliche
  Zitat gebunden.
  - Widerspruchshinweise heißen „KI-Hinweis (<provider>, ungeprüfte Schlussfolgerung)“.
  - Der Lauf-Bericht enthält `ai` und `ai_detail` (Provider, Grund, Aufrufe, Fehler,
    Wechsel).
- **Kategorien:** Die bestehenden Beleg- und Herkunftsklassen bleiben die Grundlage, also
  bestätigt, von einer Quelle berichtet, mehrfach gestützt, unbekannt und widersprüchlich.
  KI-Ergebnisse kommen als eigene Kategorie „KI-Einordnung“ bzw. „KI-Schlussfolgerung“ hinzu
  und werden nie als bestätigte Tatsache geführt.

### Datenbank (Schema 7 → 8)

- `ai_setting(key, value, updated_at, updated_by)` hält den gewählten Provider, das Modell je
  Provider, die Fallback-Regel und `nova.url`. `set_setting` lehnt Namen ab, die nach
  Schlüsseln aussehen.
- `ai_call(started_at, finished_at, task, provider, model, status, error_kind, message,
  fallback_from, request_id, seconds, research_run_id, job_id)` enthält **keine** Prompts,
  Antworten oder Schlüssel.

### Schlüssel

| System | Speicher |
|---|---|
| Windows | Windows-Anmeldeinformationsverwaltung, Ziel `MarketIntel/ai/<provider>` (Win32 `CredWriteW`/`CredReadW`/`CredDeleteW`, ohne Zusatzpaket) |
| Linux/macOS | Systemschlüsselbund über `keyring`, falls installiert. Sonst wird das Speichern **abgelehnt**. |
| NOVA zusätzlich | `NOVA_API_KEY` aus der Umgebung, nur lesend, wie NOVAs eigener Client. Gilt **nicht** für externe Anbieter. |

Die Oberfläche zeigt Schlüssel nur maskiert, etwa `sk-ant-…a1B2`. Die CLI nimmt sie nie als
Argument an, sondern nur verdeckt über `getpass` oder `--stdin`.

### NOVA-Einrichtung aus MARKT-ANALYSE

„NOVA starten und Zugang einrichten“ in den KI-Einstellungen bzw. `mi ai nova-setup` ruft
NOVAs eigene CLI auf:

1. `nova service start`
2. `nova integrations create --name markt-analyse --scope chat:complete --scope models:read`
   (falls die Integration schon existiert: `rotate`)
3. Der Schlüssel geht direkt in die Anmeldeinformationsverwaltung und wird nicht angezeigt.

## 3. Welche Funktionen nutzen KI, welche nicht

| Funktion | KI? | Prüfung |
|---|---|---|
| Suchstrategie aus dem Auftragstext (`strategie`) | ja | nur Begriffe; Firmennamen nur, wenn wörtlich im Auftrag |
| Tätigkeiten/Anwendungsfälle aus Website-Texten (`anwendungsfaelle`) | ja | Zitat muss im Dokument stehen, Schlüssel muss bekannt sein |
| Widersprüche zwischen Fakten (`widersprueche`) | ja | nur Hinweis, nur bekannte Fakt-IDs |
| Relevante Passagen, Profil-Zusammenfassung, Bedarfs-Hypothesen | vorhanden (`Assistant`) | Zitate bzw. Faktreferenzen, jede Zahl belegt |
| Quellen finden und abrufen, Register, Ausschreibungen | **nein** – Recherchekomponente | robots.txt, Limits, Protokoll |
| Website-Ermittlung (Phase G) | **nein** – belegbasierter Score | bewusst deterministisch. Eine KI-Zweitmeinung ist vorbereitet (`TASK_LABELS["website_identitaet"]`), aber nicht eingebaut. |
| Bewertung/Priorität, Fit, Segment | **nein** – Regelmodell | KI-Belege fließen nur über geprüfte Zitate ein |
| Wettbewerberanalyse, Kennzahlen, Zeitreihen | **nein** | regelbasiert mit Quelle |
