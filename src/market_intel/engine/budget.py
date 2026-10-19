"""Budget je Rechercheauftrag: Abrufe, Kandidaten, Aufgaben, KI-Aufrufe, Zeit, Kosten.

Kosten sind bei allen aktiven Quellen 0 €; das Kostenbudget (Standard 0 €) blockiert jede
künftige Quelle mit Preis je Abruf, solange niemand ausdrücklich ein Budget freigibt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Budget:
    """Obergrenzen eines Auftrags."""

    max_requests: int = 1500
    max_candidates: int = 25
    max_tasks: int = 600
    max_llm_calls: int = 100
    max_seconds: int = 3 * 3600
    max_cost_eur: float = 0.0
    max_search_hits: int = 20
    max_pages_per_site: int = 8
    max_documents_per_company: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class BudgetExhausted(Exception):
    """Ein Limit wurde erreicht; der Auftrag ist mit höherem Budget fortsetzbar."""
