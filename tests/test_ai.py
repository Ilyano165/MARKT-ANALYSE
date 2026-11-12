"""Phase H: KI-Manager, NOVA als Standard, externe Provider, sichere Schlüssel, Oberfläche.

Die Provider werden hier über ``httpx.MockTransport`` mit den **dokumentierten** Antwortformaten
von NOVA (``/api/v1``) und OpenAI angesprochen. Der Nachweis gegen eine echte NOVA mit echtem
Modell steht in ``tests/manual/test_nova_live.py`` (MI_NOVA_KEY setzen) und in PHASE_PROGRESS.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from market_intel import db
from market_intel.context import Ctx
from market_intel.engine.extract import extract_company_documents
from market_intel.llm import manager as ai
from market_intel.llm.assistant import Assistant, Fact
from market_intel.llm.providers import (
    NovaProvider,
    OpenAIProvider,
    ProviderError,
    parse_json_reply,
)
from market_intel.llm.secrets import MemorySecretStore, UnavailableSecretStore, mask
from market_intel.opportunity.model import load_model
from market_intel.products import load_products

from .fakeweb import FakeWeb

NOVA_KEY = "nova_0123456789ab_GEHEIMNIS-nova-1234"
OPENAI_KEY = "sk-proj-GEHEIMNIS-openai-abcd"


class FakeNova:
    """Antwortet wie NOVA ``/api/v1`` (Fehlerformat, Routing-Metadaten, Text statt JSON-Modus)."""

    def __init__(self, *, key: str = NOVA_KEY, model_ready: bool = True, down: bool = False):
        self.key = key
        self.model_ready = model_ready
        self.down = down
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Verbindung abgelehnt", request=request)
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(
                200, json={"status": "ok", "version": "0.2.2", "api_version": "v1"}
            )
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(
                401,
                json={"error": {"code": "invalid_api_key", "message": "x", "request_id": "r1"}},
            )
        if path == "/api/v1/models":
            models = [{"name": "local-general"}] if self.model_ready else []
            return httpx.Response(200, json={"auto_model": "nova-auto", "models": models})
        if path == "/api/v1/chat/completions":
            if not self.model_ready:
                return httpx.Response(
                    503, json={"error": {"code": "no_model", "message": "kein Modell"}}
                )
            body = json.loads(request.content)
            self.requests.append(body)
            answer = self.answer(body["messages"])
            return httpx.Response(
                200,
                headers={"x-request-id": "req_test"},
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "model": "local-general",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}}],
                    "nova": {"routing": {"model": "local-general", "category": "fast"}},
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})

    @staticmethod
    def answer(messages: list[dict[str, str]]) -> str:
        system, user = messages[0]["content"], messages[1]["content"]
        schema, _ = json.JSONDecoder().raw_decode(system[system.index("{") :])
        required = schema.get("required", [])
        data = re.search(r"<daten>\n(.*?)\n</daten>", user, re.S)
        text = data.group(1) if data else ""
        if "matches" in required:
            key = re.search(r"^- ([a-z_]+):", user, re.M)
            sentence = text.split(".")[0].strip()
            out: dict[str, Any] = {
                "matches": [
                    {"key": key.group(1) if key else "x", "quote": sentence},
                    {"key": key.group(1) if key else "x", "quote": "Erfundenes Zitat"},
                ]
            }
        elif "contradictions" in required:
            ids = re.findall(r"\[(F\d+)\]", text)
            out = {"contradictions": [{"fact_ids": ids[:2], "reason": "Sitz unterschiedlich"}]}
        else:
            out = {key: [] for key in required}
        # wie ein Modell mit Denkphase und Markdown-Zaun – der Provider muss das abfangen
        return "<think>kurz überlegen</think>\n```json\n" + json.dumps(out) + "\n```"


def openai_mock(status: int = 200, content: dict[str, Any] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {OPENAI_KEY}":
            return httpx.Response(
                401, json={"error": {"code": "invalid_api_key", "message": "Incorrect API key"}}
            )
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "x", "type": "server_error"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"content": json.dumps(content or {"passages": []})}}],
            },
        )

    return httpx.MockTransport(handler)


def manager_for(
    engine: Engine,
    store: MemorySecretStore,
    nova: FakeNova | None = None,
    openai: httpx.MockTransport | None = None,
) -> ai.AIManager:
    providers: dict[str, Any] = {}
    if nova is not None:
        providers["nova"] = NovaProvider(store.get("nova"), transport=httpx.MockTransport(nova))
    if openai is not None:
        providers["openai"] = OpenAIProvider(store.get("openai"), "gpt-test", transport=openai)
    return ai.AIManager(engine, None, store, providers=providers)


def calls(engine: Engine) -> list[dict[str, Any]]:
    return list(reversed(ai.recent_calls(engine, 100)))


# ---------------------------------------------------------------------- Auswahl


def test_without_external_key_nova_is_used(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("nova", NOVA_KEY)
    assert ai.resolve(engine, store).provider == "nova"
    ai.set_setting(engine, "provider", "anthropic")  # gewählt, aber kein Key hinterlegt
    selection = ai.resolve(engine, store)
    assert selection.provider == "nova" and "kein API-Key" in selection.reason
    nova = FakeNova()
    manager = manager_for(engine, store, nova)
    result = Assistant(manager).relevant_passages("Wir folieren Fahrzeuge.", "Tätigkeit?")
    assert result == [] and len(nova.requests) == 1  # NOVA wurde wirklich gefragt
    row = calls(engine)[-1]
    assert (row["provider"], row["model"], row["status"], row["task"]) == (
        "nova",
        "local-general",
        "ok",
        "passagen",
    )
    assert manager.name == "nova/local-general"


def test_valid_external_key_uses_selected_provider(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("openai", OPENAI_KEY)
    store.set("nova", NOVA_KEY)
    ai.set_setting(engine, "provider", "openai")
    nova = FakeNova()
    manager = manager_for(
        engine, store, nova, openai_mock(content={"passages": ["folieren Fahrzeuge"]})
    )
    quotes = Assistant(manager).relevant_passages("Wir folieren Fahrzeuge.", "Tätigkeit?")
    assert [q.quote for q in quotes] == ["folieren Fahrzeuge"]
    assert nova.requests == []  # NOVA unbeteiligt
    assert calls(engine)[-1]["provider"] == "openai" and manager.name == "openai/gpt-test"


def test_invalid_key_is_a_clear_error_and_never_falls_back(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("openai", "sk-falsch")
    store.set("nova", NOVA_KEY)
    ai.set_setting(engine, "provider", "openai")
    ai.set_setting(engine, "fallback", "nova_bei_ausfall")  # Fallback an – trotzdem kein Wechsel
    nova = FakeNova()
    manager = ai.AIManager(
        engine,
        None,
        store,
        providers={
            "openai": OpenAIProvider("sk-falsch", "gpt-test", transport=openai_mock()),
            "nova": NovaProvider(NOVA_KEY, transport=httpx.MockTransport(nova)),
        },
    )
    assistant = Assistant(manager)
    assert assistant.relevant_passages("Text.", "Frage?") == []
    assert assistant.relevant_passages("Text.", "Frage?") == []  # zweiter Versuch: blockiert
    assert nova.requests == []
    rows = calls(engine)
    assert len(rows) == 1 and rows[0]["error_kind"] == "auth"
    assert "API-Key ungültig" in rows[0]["message"]
    assert manager.summary()["errors"] and not manager.summary()["fallbacks"]
    assert not manager.available()
    probe = OpenAIProvider("sk-falsch", "gpt-test", transport=openai_mock()).probe()
    assert not probe.ok and probe.kind == "auth"


@pytest.mark.parametrize("rule", ["keiner", "nova_bei_ausfall"])
def test_unreachable_external_provider_follows_fallback_rule(engine: Engine, rule: str) -> None:
    store = MemorySecretStore()
    store.set("openai", OPENAI_KEY)
    store.set("nova", NOVA_KEY)
    ai.set_setting(engine, "provider", "openai")
    ai.set_setting(engine, "fallback", rule)
    nova = FakeNova()
    manager = manager_for(engine, store, nova, openai_mock(status=503))
    result = Assistant(manager).relevant_passages("Wir folieren Fahrzeuge.", "Tätigkeit?")
    rows = calls(engine)
    assert rows[0]["provider"] == "openai" and rows[0]["error_kind"] == "server"
    if rule == "keiner":
        assert len(rows) == 1 and nova.requests == [] and result == []
        assert manager.summary()["fallbacks"] == []
    else:
        assert rows[1]["provider"] == "nova" and rows[1]["fallback_from"] == "openai"
        assert rows[1]["status"] == "ok" and len(nova.requests) == 1
        assert "NOVA hat" in manager.summary()["fallbacks"][0]
        state = ai.status(engine, store)
        assert state["last_fallback"]["fallback_from"] == "openai"  # in der UI sichtbar


def test_nova_unreachable_gives_clear_message_and_stops_asking(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("nova", NOVA_KEY)
    nova = FakeNova(down=True)
    manager = manager_for(engine, store, nova)
    assistant = Assistant(manager)
    assert assistant.relevant_passages("Text.", "Frage?") == []
    assert assistant.relevant_passages("Text.", "Frage?") == []
    rows = calls(engine)
    assert len(rows) == 1 and rows[0]["error_kind"] == "unreachable"
    assert "nicht erreichbar" in rows[0]["message"] and "NOVA" in rows[0]["message"]
    assert not manager.available()
    probe = NovaProvider(NOVA_KEY, transport=httpx.MockTransport(nova)).probe()
    assert not probe.ok and "Läuft NOVA?" in probe.message
    no_model = NovaProvider(NOVA_KEY, transport=httpx.MockTransport(FakeNova(model_ready=False)))
    assert "kein Modell" in no_model.probe().message
    with pytest.raises(ProviderError) as exc:
        NovaProvider(None, transport=httpx.MockTransport(FakeNova())).run("t", "u", {})
    assert exc.value.kind == "not_configured"


def test_nova_must_be_local_unless_explicitly_allowed() -> None:
    from market_intel.llm.providers import LLMConfigError

    with pytest.raises(LLMConfigError):
        NovaProvider(NOVA_KEY, base_url="http://192.0.2.10:8765")


# ---------------------------------------------------------------------- Schlüssel


def test_keys_never_reach_database_logs_or_pages(
    engine: Engine, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = MemorySecretStore()
    store.set("openai", OPENAI_KEY)
    store.set("nova", NOVA_KEY)
    ai.set_setting(engine, "provider", "openai")
    Assistant(manager_for(engine, store, FakeNova(), openai_mock(status=401))).relevant_passages(
        "Text.", "Frage?"
    )
    with pytest.raises(ValueError):
        ai.set_setting(engine, "api_key.openai", OPENAI_KEY)
    raw = Path(engine.url.database or "").read_bytes()
    wal = Path(str(engine.url.database) + "-wal")
    raw += wal.read_bytes() if wal.exists() else b""
    for secret in (OPENAI_KEY, NOVA_KEY, "GEHEIMNIS"):
        assert secret.encode() not in raw
        assert secret not in caplog.text
    assert mask(OPENAI_KEY) == "sk-…abcd" and mask(NOVA_KEY) == "nova_01…1234"
    state = ai.status(engine, store)
    assert state["keys"]["openai"] == "sk-…abcd"
    assert "GEHEIMNIS" not in json.dumps(state, default=str)


def test_removed_key_is_really_deleted_and_nova_takes_over(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("anthropic", "sk-ant-api03-GEHEIM-xyz1")
    ai.set_setting(engine, "provider", "anthropic")
    assert ai.resolve(engine, store).provider == "anthropic"
    assert store.delete("anthropic") is True
    assert store.get("anthropic") is None and store.delete("anthropic") is False
    assert ai.resolve(engine, store).provider == "nova"


def test_without_secure_store_keys_are_refused_not_stored_in_plaintext() -> None:
    from market_intel.llm.secrets import SecretStoreError

    with pytest.raises(SecretStoreError, match="nicht im Klartext"):
        UnavailableSecretStore().set("openai", OPENAI_KEY)
    with pytest.raises(SecretStoreError):
        MemorySecretStore().set("openai", "zwei\nzeilen")


@pytest.mark.skipif(sys.platform != "win32", reason="nur unter Windows (läuft im Windows-Build)")
def test_windows_credential_manager_roundtrip() -> None:  # pragma: no cover - Windows
    from market_intel.llm.secrets import WindowsCredentialStore

    store = WindowsCredentialStore()
    name = "pytest_roundtrip"
    store.delete(name)
    assert store.get(name) is None
    store.set(name, "sk-test-ÄÖÜ-1234")
    assert store.get(name) == "sk-test-ÄÖÜ-1234"  # Unicode unverändert
    store.set(name, "sk-test-neu-5678")  # Ändern überschreibt
    assert store.get(name) == "sk-test-neu-5678"
    assert store.delete(name) is True
    assert store.get(name) is None and store.delete(name) is False


# ---------------------------------------------------------------------- Ergebnisse


def test_ai_results_carry_provider_and_stay_bound_to_their_source(
    engine: Engine, make_ctx: Callable[..., Ctx], cases: dict[str, Any]
) -> None:
    from market_intel import repo
    from market_intel.sources.user_data import store_document

    store = MemorySecretStore()
    store.set("nova", NOVA_KEY)
    manager = manager_for(engine, store, FakeNova())
    ctx = make_ctx(FakeWeb(), assistant=Assistant(manager))
    with engine.begin() as conn:
        cid = repo.create_company(conn, "Beispiel Fensterbau GmbH")
    text = (
        "Wir montieren Sonnenschutzfolien an Glasfassaden in ganz Bayern. "
        "Außerdem bieten wir Wartung an."
    )
    doc = store_document(
        engine,
        "user_upload",
        f"<html><body><p>{text}</p></body></html>".encode(),
        url="https://beispiel.test/leistungen",
        title="Leistungen",
        publisher=None,
        published=None,
        content_type="text/html",
    )
    extract_company_documents(ctx, cid, [doc], cases, [])
    with engine.connect() as conn:
        rows = (
            conn.execute(select(db.evidence).where(db.evidence.c.extraction_method.like("llm:%")))
            .mappings()
            .all()
        )
    assert rows, "KI-Einordnung fehlt"
    for row in rows:
        assert row["extraction_method"] == "llm:nova/local-general"  # Provider + Modell
        assert row["source_document_id"] == doc  # an die Quelle gebunden
        assert row["quote"] in text and row["quote_verified"]
    assert all("Erfundenes" not in r["quote"] for r in rows)  # erfundenes Zitat verworfen
    facts = [Fact("F1", "Sitz: München"), Fact("F2", "Sitz: Hamburg")]
    hints = Assistant(manager).flag_contradictions(facts)
    assert hints == [{"fact_ids": ["F1", "F2"], "reason": "Sitz unterschiedlich"}]


def test_research_run_records_provider_and_ai_calls(
    engine: Engine, run_research: Callable[..., Any]
) -> None:
    from .test_end_to_end import PROOF_REQUEST

    store = MemorySecretStore()
    store.set("nova", NOVA_KEY)
    manager = manager_for(engine, store, FakeNova())
    _ctx, run_id, status = run_research(PROOF_REQUEST, assistant=Assistant(manager))
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        summary = json.loads(
            conn.execute(
                select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
            ).scalar_one()
        )
        bound = (
            conn.execute(
                select(db.ai_call.c.research_run_id).where(db.ai_call.c.provider == "nova")
            )
            .scalars()
            .all()
        )
    assert summary["ai"] == "nova/local-general"
    assert summary["ai_detail"]["provider"] == "nova" and summary["ai_detail"]["calls"] > 0
    assert bound and set(bound) == {run_id}  # jeder Aufruf dem Auftrag zugeordnet


def test_nova_down_research_still_completes_and_says_so(
    engine: Engine, run_research: Callable[..., Any]
) -> None:
    from .test_end_to_end import PROOF_REQUEST

    store = MemorySecretStore()
    store.set("nova", NOVA_KEY)
    manager = manager_for(engine, store, FakeNova(down=True))
    _ctx, run_id, status = run_research(PROOF_REQUEST, assistant=Assistant(manager))
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        summary = json.loads(
            conn.execute(
                select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
            ).scalar_one()
        )
    detail = summary["ai_detail"]
    assert detail["calls"] == 0 and any("nicht erreichbar" in e for e in detail["errors"])


def test_parse_json_reply_handles_thinking_and_fences() -> None:
    assert parse_json_reply('<think>{"x": 1}</think> Antwort: {"a": [1]}') == {"a": [1]}
    assert parse_json_reply('```json\n{"b": "}"}\n```') == {"b": "}"}
    with pytest.raises(ProviderError) as exc:
        parse_json_reply("leider keine Daten")
    assert exc.value.kind == "invalid_output"


# ---------------------------------------------------------------------- Oberfläche


def _post(base: str, path: str, token: str, **fields: str) -> tuple[str, str]:
    data = urllib.parse.urlencode({"csrf": token, **fields}).encode()
    with urllib.request.urlopen(f"{base}{path}", data=data) as response:
        return response.geturl(), response.read().decode()


def test_ui_ai_settings_switch_persists_after_restart(
    engine: Engine, cases: dict[str, Any], tmp_path: Path
) -> None:
    from market_intel.ui.server import make_server

    store = MemorySecretStore()
    discovery = (cases, load_products(), load_model())

    def start() -> tuple[Any, str]:
        server = make_server(engine, "127.0.0.1", 0, discovery, secret_store=store)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    server, base = start()
    try:
        page = urllib.request.urlopen(f"{base}/settings/ai").read().decode()
        assert "KI-Einstellungen" in page and "Aktiver KI-Provider" in page
        assert "NOVA" in page and "Anthropic (Claude)" in page and "OpenAI" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        url, body = _post(base, "/settings/ai/key", token, provider="openai", key=OPENAI_KEY)
        assert "GEHEIMNIS" not in body and "sk-…abcd" in body  # nur maskiert
        assert store.get("openai") == OPENAI_KEY
        url, body = _post(base, "/settings/ai/use", token, provider="openai")
        assert "Aktiv: OpenAI" in urllib.parse.unquote(url)
        chip = json.loads(urllib.request.urlopen(f"{base}/ai/status.json").read())
        assert chip["provider"] == "openai" and chip["label"] == "OpenAI"
        _post(base, "/settings/ai/fallback", token, rule="nova_bei_ausfall")
        bad = _post(base, "/settings/ai/use", token, provider="erfunden")[0]
        assert "err=" in bad
    finally:
        server.shutdown()
        server.server_close()

    engine.dispose()  # „Neustart“: neue Verbindung, neuer Server, gleiche Datenbank
    restarted = db.make_engine(str(engine.url))
    assert ai.resolve(restarted, store).provider == "openai"
    assert ai.get_settings(restarted)["fallback"] == "nova_bei_ausfall"
    server, base = start()
    try:
        page = urllib.request.urlopen(f"{base}/settings/ai").read().decode()
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        assert "sk-…abcd" in page and OPENAI_KEY not in page
        url, body = _post(base, "/settings/ai/key-remove", token, provider="openai")
        assert store.get("openai") is None and "Aktiv: NOVA" in urllib.parse.unquote(url)
        url, _ = _post(base, "/settings/ai/use", token, provider="nova")
        assert ai.get_settings(restarted)["provider"] == "nova"
        jobs_page = urllib.request.urlopen(f"{base}/jobs/new").read().decode()
        assert "KI-Analyse verwenden" in jobs_page and "aktiv: NOVA" in jobs_page
    finally:
        server.shutdown()
        server.server_close()


def test_desktop_does_not_take_novas_port() -> None:
    import argparse

    from market_intel import desktop

    source = Path(desktop.__file__).read_text(encoding="utf-8")
    assert "default=8765" not in source and "default=8780" in source
    assert isinstance(argparse.ArgumentParser, type)


def test_nova_setup_uses_novas_own_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("Attrappe als Shell-Skript – unter Windows nicht ausführbar")
    from market_intel.llm import nova_setup

    log = tmp_path / "aufrufe.txt"
    fake = tmp_path / "nova"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'if [ "$1" = "integrations" ] && [ "$2" = "create" ]; then\n'
        '  echo \'{"name": "markt-analyse", "key_prefix": "nova_abc", '
        '"api_key": "nova_abc_GEHEIM"}\'; exit 0; fi\n'
        'if [ "$1" = "service" ]; then echo \'{"status": "running"}\'; exit 0; fi\n'
        "exit 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("NOVA_CLI", str(fake))
    assert nova_setup.find_cli() == fake
    store = MemorySecretStore()
    assert nova_setup.start_service(fake)["status"] == "running"
    assert nova_setup.create_access(fake, store) == "nova_abc"
    assert store.get("nova") == "nova_abc_GEHEIM"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "service start",
        "integrations create --name markt-analyse --scope chat:complete --scope models:read",
    ]


def test_bare_list_is_wrapped_only_for_single_list_schema() -> None:
    """Live-Befund 2026-11-12 (Qwen3 0.6B über NOVA): Antwort war nur die Liste."""
    schema = {
        "type": "object",
        "properties": {"matches": {"type": "array", "items": {"type": "object"}}},
        "required": ["matches"],
    }
    reply = '<think>…</think>\n```json\n[{"key": "a", "quote": "b"}]\n```'
    assert parse_json_reply(reply, schema) == {"matches": [{"key": "a", "quote": "b"}]}
    two = {"type": "object", "properties": {}, "required": ["a", "b"]}
    assert parse_json_reply('[1] {"a": 1, "b": 2}', two) == {"a": 1, "b": 2}


def test_nova_key_from_environment_only_for_nova(monkeypatch: pytest.MonkeyPatch) -> None:
    from market_intel.llm.secrets import EnvNovaStore

    monkeypatch.setenv("NOVA_API_KEY", NOVA_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-aus-umgebung")
    store = EnvNovaStore(MemorySecretStore())
    assert store.get("nova") == NOVA_KEY
    assert store.get("anthropic") is None  # externe Anbieter nie still über die Umgebung


def test_unexpected_provider_crash_is_logged_not_left_running(engine: Engine) -> None:
    class Kaputt:
        name = "nova"
        model = "x"
        calls = 0

        def run(self, task: str, user: str, schema: dict[str, Any]) -> Any:
            raise RuntimeError("unerwartet")

    manager = ai.AIManager(engine, None, MemorySecretStore(), providers={"nova": Kaputt()})
    assert Assistant(manager).relevant_passages("Text.", "Frage?") == []
    row = calls(engine)[-1]
    assert row["status"] == "fehler" and row["error_kind"] == "intern"
    assert ai.running_task(engine) is None
