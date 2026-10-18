"""Laufzeitkontext eines Rechercheauftrags."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine

from .config import Settings
from .http.gateway import FetchGateway
from .llm.local import LocalLLM, NullLLM


@dataclass(slots=True)
class Ctx:
    """Alles, was Adapter und Dienste brauchen."""

    engine: Engine
    gateway: FetchGateway
    settings: Settings
    llm: LocalLLM = field(default_factory=NullLLM)
    run_id: int | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        """Hinweis für den Run-Bericht (z. B. Quelle nicht erreichbar)."""
        self.notes.append(message)
