"""Echter Test gegen eine laufende NOVA (kein Mock): MI_NOVA_KEY setzen, optional MI_NOVA_URL.

    MI_NOVA_KEY=nova_… python -m pytest tests/manual/test_nova_live.py -s

Prüft den vollständigen Weg MARKT-ANALYSE → KI-Manager → NOVA ``/api/v1`` → NOVA-Router →
lokales Modell und zurück – einschließlich der deterministischen Prüfung der Antwort.
"""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import Engine

from market_intel.extraction.text import quote_in_text
from market_intel.llm import manager as ai
from market_intel.llm.assistant import Assistant
from market_intel.llm.providers import NovaProvider
from market_intel.llm.secrets import MemorySecretStore

KEY = os.environ.get("MI_NOVA_KEY", "")
URL = os.environ.get("MI_NOVA_URL", NovaProvider.DEFAULT_URL)
pytestmark = pytest.mark.skipif(not KEY, reason="manueller Live-Test: MI_NOVA_KEY setzen")

TEXT = (
    "Die Muster Folientechnik GmbH foliert Fahrzeuge und beschriftet Schaufenster in Köln. "
    "Außerdem montieren wir Sonnenschutzfolien an Glasfassaden."
)


def test_real_nova_probe_and_checked_classification(engine: Engine) -> None:
    store = MemorySecretStore()
    store.set("nova", KEY)
    if URL != NovaProvider.DEFAULT_URL:
        ai.set_setting(engine, "nova.url", URL)
    probe = ai.probe(engine, "nova", store)
    print("\nVerbindungstest:", probe)
    assert probe.ok, probe.message
    manager = ai.AIManager(engine, None, store)
    assert manager.selection.provider == "nova"
    started = time.monotonic()
    matches = Assistant(manager).classify_use_cases(
        TEXT,
        {
            "fahrzeugfolierung": "Fahrzeugfolierung",
            "glasfolien": "Glas- und Fensterfolien",
            "zahnmedizin": "Zahnarztpraxis",
        },
    )
    print("Dauer:", round(time.monotonic() - started, 1), "s · Ergebnis:", matches)
    rows = ai.recent_calls(engine, 5)
    print("Protokoll:", [(r["provider"], r["model"], r["status"], r["message"]) for r in rows])
    assert rows[0]["provider"] == "nova" and rows[0]["status"] in ("ok", "fehler")
    for match in matches:  # was übrig bleibt, ist wörtlich belegt und bekannt
        assert quote_in_text(match.quote, TEXT)  # Prüfregel der Anwendung (Groß/klein egal)
        assert match.label in ("fahrzeugfolierung", "glasfolien", "zahnmedizin")
    assert manager.name.startswith("nova/")


def test_real_nova_rejects_wrong_key(engine: Engine) -> None:
    probe = NovaProvider("nova_000000000000_falsch", URL).probe()
    print("\nFalscher Schlüssel:", probe)
    assert not probe.ok and probe.kind == "auth"
