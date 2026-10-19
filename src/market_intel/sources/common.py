"""Gemeinsame Hilfen für Quellenadapter: robuste Auswertung fehlerhafter Antworten."""

from __future__ import annotations

import json
from typing import Any

from ..context import Ctx
from ..http.gateway import FetchResult


def json_object(ctx: Ctx, result: FetchResult) -> dict[str, Any]:
    """Antwort als JSON-Objekt. Ungültiges JSON, HTML-Fehlerseiten oder Listen statt Objekt
    ergeben ``{}`` und einen Hinweis – der Adapter liefert dann keine Treffer statt abzustürzen."""
    try:
        data = json.loads(result.content)
    except (ValueError, UnicodeDecodeError):
        ctx.note(
            f"Unerwartete Antwort von {result.url[:120]}: kein gültiges JSON "
            f"(Anfang: {result.content[:60]!r})"
        )
        return {}
    if not isinstance(data, dict):
        ctx.note(f"Unerwartete Antwort von {result.url[:120]}: JSON ist kein Objekt")
        return {}
    return data


def as_dict(value: Any) -> dict[str, Any]:
    """Wert als Objekt oder leeres Objekt (für unvollständige Antworten)."""
    return value if isinstance(value, dict) else {}


def dict_items(value: Any) -> list[dict[str, Any]]:
    """Nur die Objekte einer Liste; alles andere wird verworfen."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
