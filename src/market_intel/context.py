"""Laufzeitkontext eines Rechercheauftrags."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine

from .config import Settings
from .engine.budget import Budget
from .http.gateway import FetchGateway
from .llm.assistant import Assistant


@dataclass(slots=True)
class Ctx:
    """Alles, was Adapter und Dienste brauchen."""

    engine: Engine
    gateway: FetchGateway
    settings: Settings
    ai: Assistant = field(default_factory=Assistant)
    budget: Budget = field(default_factory=Budget)
    run_id: int | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        """Hinweis für den Run-Bericht (z. B. Quelle nicht erreichbar)."""
        self.notes.append(message)
