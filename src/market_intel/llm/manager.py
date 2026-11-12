"""KI-Manager: einzige Stelle, über die alle KI-Aufgaben der Anwendung laufen.

Auswahlregel (nachvollziehbar, in der Oberfläche sichtbar):

1. Vom Nutzer ausdrücklich gewählter externer Provider **mit hinterlegtem Key**.
2. Sonst **NOVA** (Standard) – auch wenn ein externer Provider gewählt, aber kein Key
   hinterlegt ist.
3. Ist der gewählte Provider nicht nutzbar, gibt es eine verständliche Fehlermeldung; die KI-Aufgabe
   findet dann **nicht** statt (die Recherche läuft regelbasiert weiter und sagt das).

Ein ungültiger Key führt nie zu einem stillen Wechsel. Ein Wechsel zu NOVA geschieht nur, wenn die
Fallback-Regel ``nova_bei_ausfall`` gesetzt ist **und** der externe Anbieter ausfällt (nicht
erreichbar, Zeitüberschreitung, Überlastung, Anfragelimit) – jeder Wechsel wird in ``ai_call``
protokolliert und angezeigt.

Die Recherche bleibt getrennt: Der Manager beschafft keine Quellen; er beantwortet nur Aufgaben über
bereits beschaffte Texte. Was die KI liefert, prüft ``Assistant`` deterministisch (wörtliche Zitate,
Faktreferenzen) – sonst wird es verworfen.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, select

from .. import db
from ..config import LLMSettings
from .providers import (
    ERROR_LABELS,
    PROVIDERS,
    AnthropicProvider,
    LLMConfigError,
    NovaProvider,
    OpenAIProvider,
    Probe,
    ProviderError,
)
from .secrets import SecretStore, SecretStoreError, default_store, mask

#: Bekannte Provider. ``external``: Daten verlassen den Rechner (Anbieter-API, eigener Key).
PROVIDER_INFO: dict[str, dict[str, Any]] = {
    "nova": {
        "label": "NOVA",
        "long": "NOVA – eigene KI, läuft lokal auf diesem Rechner",
        "external": False,
        "default_model": "nova-auto",
        "key_label": "NOVA-Zugangsschlüssel (nova_…)",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "long": "Anthropic Claude – externer Dienst, eigener API-Key, Kosten beim Anbieter",
        "external": True,
        "default_model": AnthropicProvider.DEFAULT_MODEL,
        "key_label": "Anthropic-API-Key (sk-ant-…)",
    },
    "openai": {
        "label": "OpenAI",
        "long": "OpenAI – externer Dienst, eigener API-Key, Kosten beim Anbieter",
        "external": True,
        "default_model": "",
        "key_label": "OpenAI-API-Key (sk-…)",
    },
    "lokal": {
        "label": "Lokaler Server (Konfiguration)",
        "long": "Ollama oder OpenAI-kompatibler Server aus market_intel.toml [llm]",
        "external": False,
        "default_model": "",
        "key_label": "",
    },
}
EXTERNAL = tuple(k for k, v in PROVIDER_INFO.items() if v["external"])

FALLBACK_RULES = {
    "keiner": "Kein automatischer Wechsel: Fällt der gewählte Anbieter aus, findet die KI-Aufgabe "
    "nicht statt.",
    "nova_bei_ausfall": "Fällt der externe Anbieter aus (nicht erreichbar, Zeitüberschreitung, "
    "Überlastung), übernimmt NOVA – nie bei ungültigem Key. Jeder Wechsel wird angezeigt.",
}

TASK_LABELS = {
    "strategie": "Suchstrategie aus dem Auftragstext",
    "passagen": "Relevante Textstellen finden",
    "anwendungsfaelle": "Tätigkeiten / Anwendungsfälle erkennen",
    "zusammenfassung": "Profil aus belegten Fakten zusammenfassen",
    "bedarfe": "Mögliche Bedarfe ableiten (Hypothesen)",
    "widersprueche": "Widersprüche zwischen Angaben erkennen",
    "website_identitaet": "Website-Zuordnung prüfen",
    "test": "Verbindungstest",
    "allgemein": "KI-Aufgabe",
}

#: Fehler, nach denen der Provider im laufenden Auftrag nicht erneut gefragt wird.
_BLOCKING = frozenset({"auth", "permission", "not_configured", "unavailable", "limit"})

_probe_lock = threading.Lock()
_last_probe: dict[str, tuple[float, Probe]] = {}


# ---------------------------------------------------------------------- Einstellungen


def get_settings(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(select(db.ai_setting.c.key, db.ai_setting.c.value)).all()
    return {str(k): str(v) for k, v in rows}


def set_setting(engine: Engine, key: str, value: str | None, by: str = "") -> None:
    """Einstellung setzen (``None`` löscht). Für Geheimnisse ist ``SecretStore`` zuständig."""
    if key.startswith(("key", "secret", "token")) or "api_key" in key:
        raise ValueError("Schlüssel gehören in den sicheren Speicher, nicht in die Datenbank")
    with engine.begin() as conn:
        conn.execute(db.ai_setting.delete().where(db.ai_setting.c.key == key))
        if value is not None:
            conn.execute(
                db.ai_setting.insert().values(
                    key=key, value=value, updated_at=db.utcnow(), updated_by=by[:80] or None
                )
            )


# ---------------------------------------------------------------------- Auswahl


@dataclass(frozen=True, slots=True)
class Selection:
    provider: str
    reason: str
    chosen: str | None  # was der Nutzer gewählt hat (None = automatisch)


def resolve(engine: Engine, store: SecretStore, llm: LLMSettings | None = None) -> Selection:
    """Welcher Provider gilt gerade – und warum."""
    chosen = get_settings(engine).get("provider") or None
    if chosen in EXTERNAL:
        if _safe_get(store, chosen):
            return Selection(chosen, "vom Nutzer gewählt", chosen)
        return Selection(
            "nova",
            f"{PROVIDER_INFO[chosen]['label']} gewählt, aber kein API-Key hinterlegt – NOVA ist "
            "Standard",
            chosen,
        )
    if chosen == "lokal":
        if llm is not None and llm.enabled:
            return Selection("lokal", "vom Nutzer gewählt (Server aus der Konfiguration)", chosen)
        return Selection(
            "nova", "Lokaler Server gewählt, aber in der Konfiguration ausgeschaltet – NOVA", chosen
        )
    return Selection("nova", "Standard (kein externer Anbieter gewählt)", chosen)


def _safe_get(store: SecretStore, name: str) -> str | None:
    try:
        return store.get(name)
    except SecretStoreError:
        return None


def model_for(engine: Engine, provider: str, settings: dict[str, str] | None = None) -> str:
    values = settings if settings is not None else get_settings(engine)
    return values.get(f"model.{provider}") or str(PROVIDER_INFO[provider]["default_model"])


def build(
    engine: Engine,
    name: str,
    store: SecretStore,
    llm: LLMSettings | None = None,
    *,
    max_calls: int = 200,
    transport: Any = None,
) -> Any:
    """Provider-Objekt bauen (wirft ``ProviderError`` bei unbrauchbarer Konfiguration)."""
    values = get_settings(engine)
    model = model_for(engine, name, values) if name in PROVIDER_INFO else ""
    if name == "nova":
        try:
            return NovaProvider(
                _safe_get(store, "nova"),
                values.get("nova.url") or NovaProvider.DEFAULT_URL,
                model,
                transport=transport,
                max_calls=max_calls,
            )
        except LLMConfigError as exc:
            raise ProviderError("not_configured", str(exc)) from exc
    if name == "anthropic":
        return AnthropicProvider(_safe_get(store, "anthropic"), model, max_calls=max_calls)
    if name == "openai":
        return OpenAIProvider(
            _safe_get(store, "openai"), model, transport=transport, max_calls=max_calls
        )
    if name == "lokal":
        if llm is None or not llm.enabled:
            raise ProviderError("not_configured", "Lokaler Server ist in der Konfiguration aus")
        cls = PROVIDERS.get(llm.provider)
        if cls is None:
            raise ProviderError("not_configured", f"Unbekannter lokaler Server {llm.provider!r}")
        try:
            return cls(llm, transport=transport, max_calls=max_calls)
        except LLMConfigError as exc:
            raise ProviderError("not_configured", str(exc)) from exc
    raise ProviderError("not_configured", f"Unbekannter Provider {name!r}")


def probe(
    engine: Engine,
    name: str,
    store: SecretStore,
    llm: LLMSettings | None = None,
    transport: Any = None,
) -> Probe:
    """Verbindung testen und Ergebnis für die Statusanzeige merken."""
    try:
        result: Probe = build(engine, name, store, llm, transport=transport).probe()
    except ProviderError as exc:
        result = Probe(False, str(exc), exc.kind)
    with _probe_lock:
        _last_probe[name] = (time.time(), result)
    return result


def last_probe(name: str) -> tuple[float, Probe] | None:
    with _probe_lock:
        return _last_probe.get(name)


def forget_probe(name: str | None = None) -> None:
    with _probe_lock:
        if name is None:
            _last_probe.clear()
        else:
            _last_probe.pop(name, None)


# ---------------------------------------------------------------------- Manager


class AIManager:
    """Erfüllt die ``LLMProvider``-Schnittstelle – ``Assistant`` merkt keinen Unterschied."""

    def __init__(
        self,
        engine: Engine,
        llm: LLMSettings | None = None,
        store: SecretStore | None = None,
        *,
        max_calls: int = 200,
        transport: Any = None,
        providers: dict[str, Any] | None = None,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.store = store if store is not None else default_store()
        self.max_calls = max_calls
        self.transport = transport
        self._providers: dict[str, Any] = dict(providers or {})
        self._blocked: dict[str, ProviderError] = {}
        self.calls = 0
        self.research_run_id: int | None = None
        self.job_id: int | None = None
        self.selection = resolve(engine, self.store, llm)
        self.last_used: tuple[str, str] | None = None
        self.errors: list[str] = []
        self.fallbacks: list[str] = []

    # --- Schnittstelle wie ein Provider -------------------------------------------------

    @property
    def name(self) -> str:
        if self.last_used:
            return f"{self.last_used[0]}/{self.last_used[1]}"
        return self.selection.provider

    def bind(self, *, research_run_id: int | None = None, job_id: int | None = None) -> None:
        self.research_run_id = research_run_id
        self.job_id = job_id

    def available(self) -> bool:
        """Ob KI-Aufgaben versucht werden – nicht blockiert und Provider gebaut."""
        name = self.selection.provider
        if name in self._blocked:
            return False
        try:
            self._provider(name)
        except ProviderError as exc:
            self._block(name, exc, task="allgemein")
            return False
        return True

    def complete_json(self, task: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        return self.complete_task("allgemein", task, user, schema)

    def complete_task(
        self, kind: str, task: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        primary = self.selection.provider
        if primary in self._blocked:
            return None
        try:
            return self._run(primary, kind, task, user, schema, fallback_from=None)
        except ProviderError as exc:
            if (
                exc.retryable
                and primary in EXTERNAL
                and get_settings(self.engine).get("fallback") == "nova_bei_ausfall"
                and "nova" not in self._blocked
            ):
                note = (
                    f"{PROVIDER_INFO[primary]['label']} ausgefallen ({exc.label}) – NOVA hat "
                    f"„{TASK_LABELS.get(kind, kind)}“ übernommen (Fallback-Regel)"
                )
                self.fallbacks.append(note)
                try:
                    return self._run("nova", kind, task, user, schema, fallback_from=primary)
                except ProviderError:
                    return None
            return None

    # --- intern ------------------------------------------------------------------------

    def _provider(self, name: str) -> Any:
        if name not in self._providers:
            self._providers[name] = build(
                self.engine,
                name,
                self.store,
                self.llm,
                max_calls=self.max_calls,
                transport=self.transport,
            )
        return self._providers[name]

    def _block(self, name: str, exc: ProviderError, *, task: str) -> None:
        if name in self._blocked:
            return
        self._blocked[name] = exc
        message = f"{PROVIDER_INFO.get(name, {}).get('label', name)}: {exc}"
        self.errors.append(message)
        self._log_done(self._log_start(task, name, None), "fehler", exc.kind, str(exc))

    def _run(
        self,
        name: str,
        kind: str,
        task: str,
        user: str,
        schema: dict[str, Any],
        *,
        fallback_from: str | None,
    ) -> dict[str, Any]:
        try:
            provider = self._provider(name)
        except ProviderError as exc:
            self._block(name, exc, task=kind)
            raise
        call_id = self._log_start(kind, name, getattr(provider, "model", None), fallback_from)
        data: dict[str, Any]
        try:
            if hasattr(provider, "run"):
                reply = provider.run(task, user, schema)
                data, model = reply.data, reply.model
                request_id, seconds = reply.request_id, reply.seconds
            else:  # alter Provider ohne einheitliches Format (nur Tests/Erweiterungen)
                result = provider.complete_json(task, user, schema)
                if result is None:
                    raise ProviderError("invalid_output", "keine verwertbare Antwort")
                data, model = result, str(getattr(provider, "model", "") or name)
                request_id, seconds = None, 0.0
        except ProviderError as exc:
            self._log_done(call_id, "fehler", exc.kind, str(exc))
            if exc.kind in _BLOCKING or (name == "nova" and exc.kind == "unreachable"):
                self._blocked[name] = exc
                self.errors.append(f"{PROVIDER_INFO.get(name, {}).get('label', name)}: {exc}")
            raise
        except Exception as exc:  # unerwarteter Fehler: nie als „läuft“ stehen lassen
            self._log_done(call_id, "fehler", "intern", f"{type(exc).__name__}: {exc}"[:300])
            self.errors.append(f"{name}: interner Fehler ({type(exc).__name__})")
            raise ProviderError("server", f"interner Fehler: {type(exc).__name__}") from exc
        self.calls += 1
        self.last_used = (name, model)
        self._log_done(
            call_id, "ok", None, None, model=model, request_id=request_id, seconds=seconds
        )
        return data

    def _log_start(
        self, kind: str, provider: str, model: str | None, fallback_from: str | None = None
    ) -> int:
        with self.engine.begin() as conn:
            return int(
                conn.execute(
                    db.ai_call.insert().values(
                        started_at=db.utcnow(),
                        task=kind[:40],
                        provider=provider,
                        model=(model or None),
                        status="laeuft",
                        fallback_from=fallback_from,
                        research_run_id=self.research_run_id,
                        job_id=self.job_id,
                    )
                ).inserted_primary_key[0]
            )

    def _log_done(
        self,
        call_id: int,
        status: str,
        error_kind: str | None,
        message: str | None,
        *,
        model: str | None = None,
        request_id: str | None = None,
        seconds: float | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "finished_at": db.utcnow(),
            "status": status,
            "error_kind": error_kind,
            "message": (message or "")[:400] or None,
        }
        if model:
            values["model"] = model[:120]
        if request_id:
            values["request_id"] = request_id[:80]
        if seconds is not None:
            values["seconds"] = seconds
        with self.engine.begin() as conn:
            conn.execute(db.ai_call.update().where(db.ai_call.c.id == call_id).values(**values))

    def summary(self) -> dict[str, Any]:
        """Für Lauf-Berichte: wer hat analysiert, was ging schief, welche Wechsel gab es."""
        return {
            "provider": self.selection.provider,
            "reason": self.selection.reason,
            "used": self.name if self.last_used else None,
            "calls": self.calls,
            "errors": list(self.errors),
            "fallbacks": list(self.fallbacks),
        }


# ---------------------------------------------------------------------- Status für die UI


def recent_calls(engine: Engine, limit: int = 50) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.ai_call).order_by(db.ai_call.c.id.desc()).limit(limit)
        ).mappings()
        return [dict(r) for r in rows]


def running_task(engine: Engine) -> dict[str, Any] | None:
    """Gerade laufende KI-Aufgabe (nicht älter als 15 Minuten)."""
    since = db.utcnow() - timedelta(minutes=15)
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(db.ai_call)
                .where(db.ai_call.c.status == "laeuft", db.ai_call.c.started_at >= since)
                .order_by(db.ai_call.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def status(engine: Engine, store: SecretStore, llm: LLMSettings | None = None) -> dict[str, Any]:
    """Zustand für Statusleiste und KI-Einstellungen – ohne Schlüsselwerte."""
    selection = resolve(engine, store, llm)
    values = get_settings(engine)
    keys: dict[str, str] = {}
    for name in PROVIDER_INFO:
        if name == "lokal":
            continue
        keys[name] = mask(_safe_get(store, name))
    probe_entry = last_probe(selection.provider)
    with engine.connect() as conn:
        last_error = (
            conn.execute(
                select(db.ai_call)
                .where(db.ai_call.c.status == "fehler")
                .order_by(db.ai_call.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        last_fallback = (
            conn.execute(
                select(db.ai_call)
                .where(db.ai_call.c.fallback_from.is_not(None))
                .order_by(db.ai_call.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
    connection = "ungeprüft"
    if probe_entry:
        connection = "verbunden" if probe_entry[1].ok else "Fehler"
    return {
        "selection": selection,
        "provider": selection.provider,
        "label": PROVIDER_INFO[selection.provider]["label"],
        "model": model_for(engine, selection.provider, values) or None,
        "fallback": values.get("fallback", "keiner"),
        "keys": keys,
        "store": store.label,
        "connection": connection,
        "probe": probe_entry[1] if probe_entry else None,
        "probed_at": probe_entry[0] if probe_entry else None,
        "running": running_task(engine),
        "last_error": dict(last_error) if last_error else None,
        "last_fallback": dict(last_fallback) if last_fallback else None,
        "error_labels": ERROR_LABELS,
    }


def check_at_startup(
    engine: Engine, store: SecretStore | None = None, llm: LLMSettings | None = None
) -> threading.Thread:
    """Beim Programmstart im Hintergrund prüfen, welche Provider verfügbar sind."""

    def work() -> None:
        try:
            chosen_store = store if store is not None else default_store()
            names = {"nova", resolve(engine, chosen_store, llm).provider}
            for name in sorted(names):
                probe(engine, name, chosen_store, llm)
        except Exception:
            pass

    thread = threading.Thread(target=work, name="ki-statuspruefung", daemon=True)
    thread.start()
    return thread
