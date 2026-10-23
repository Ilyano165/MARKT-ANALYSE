"""Opportunity-Modell laden und prüfen (Gewichte, Schwellen, Segmente)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

OPERATORS = (">=", "<=", ">", "<", "==", "in", "not_in")
FIELDS = (
    "product_relevance",
    "business_potential",
    "data_quality",
    "growth_status",
    "growth_basis",
    "expansion",
    "size_class",
    "core_application",
    "application_status",
    "is_customer",
    "rejected",
)


@dataclass(frozen=True, slots=True)
class Segment:
    """Segment mit erklärbaren Bedingungen."""

    key: str
    name: str
    description: str
    conditions: tuple[tuple[str, str, Any], ...]


@dataclass(frozen=True, slots=True)
class Model:
    """Vollständiges, geprüftes Bewertungsmodell."""

    version: str
    raw: dict[str, Any]
    segments: tuple[Segment, ...]

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        assert isinstance(value, dict)
        return value


class ModelError(ValueError):
    """Ungültiges Modell (mit Begründung)."""


def load_model(path: Path | None = None) -> Model:
    """Eigene Datei oder mitgeliefertes Modell; wird immer geprüft."""
    if path is None:
        text = resources.files("market_intel.data").joinpath("opportunity.toml").read_text("utf-8")
    else:
        text = path.read_text("utf-8")
    raw = tomllib.loads(text)
    problems = check(raw)
    if problems:
        raise ModelError("; ".join(problems))
    segments = tuple(
        Segment(
            str(s["key"]),
            str(s["name"]),
            str(s.get("description", "")),
            tuple((str(c[0]), str(c[1]), c[2]) for c in s.get("conditions", [])),
        )
        for s in raw.get("segment", [])
    )
    return Model(str(raw["model"]["version"]), raw, segments)


def _sum_one(weights: dict[str, float], name: str, problems: list[str]) -> None:
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 0.001:
        problems.append(f"{name}: Gewichte ergeben {total:.3f} statt 1")
    if any(float(v) < 0 for v in weights.values()):
        problems.append(f"{name}: negative Gewichte sind unzulässig")


def check(raw: dict[str, Any]) -> list[str]:
    """Regeln: Gewichte summieren sich zu 1; Produkt-Fit wiegt mehr als Größe; Segmente gültig."""
    problems: list[str] = []
    try:
        priority = raw["model"]["priority_weights"]
        relevance = raw["product_relevance"]["weights"]
        potential = raw["business_potential"]["weights"]
        quality = raw["data_quality"]["weights"]
    except KeyError as exc:
        return [f"Abschnitt fehlt: {exc}"]
    _sum_one(priority, "model.priority_weights", problems)
    _sum_one(relevance, "product_relevance.weights", problems)
    _sum_one(potential, "business_potential.weights", problems)
    _sum_one(quality, "data_quality.weights", problems)
    w_fit = float(priority.get("product_relevance", 0))
    w_size = float(priority.get("business_potential", 0)) * float(potential.get("size", 0))
    if w_fit < 0.5:
        problems.append("Produktrelevanz muss mindestens 50 % der Priorität ausmachen")
    if w_fit <= w_size:
        problems.append("Produkt-Fit muss stärker gewichtet sein als Unternehmensgröße")
    segments = raw.get("segment", [])
    if not segments:
        problems.append("keine Segmente definiert")
    keys = set()
    for segment in segments:
        key = segment.get("key")
        if not key or key in keys:
            problems.append(f"Segment ohne oder mit doppeltem Schlüssel: {key}")
        keys.add(key)
        for condition in segment.get("conditions", []):
            if len(condition) != 3 or condition[0] not in FIELDS or condition[1] not in OPERATORS:
                problems.append(f"Segment {key}: ungültige Bedingung {condition}")
    if segments and segments[-1].get("conditions"):
        problems.append("Das letzte Segment muss ohne Bedingungen sein (Auffangsegment)")
    return problems


def evaluate(condition: tuple[str, str, Any], facts: dict[str, Any]) -> bool:
    """Eine Bedingung gegen die Merkmale prüfen; unbekannte Zahl erfüllt keine Zahlenbedingung."""
    field, op, value = condition
    actual = facts.get(field)
    if op in ("in", "not_in"):
        return (actual in value) == (op == "in")
    if op == "==":
        return bool(actual == value)
    if actual is None:
        return False
    number, limit = float(actual), float(value)
    return {
        ">=": number >= limit,
        "<=": number <= limit,
        ">": number > limit,
        "<": number < limit,
    }[op]


def assign_segment(model: Model, facts: dict[str, Any]) -> tuple[Segment, list[str]]:
    """Erstes passendes Segment und die erfüllten Bedingungen als Begründung."""
    for segment in model.segments:
        if all(evaluate(c, facts) for c in segment.conditions):
            reasons = [f"{f} {op} {v} (Wert: {facts.get(f)})" for f, op, v in segment.conditions]
            return segment, reasons or ["keine andere Segmentregel erfüllt"]
    raise ModelError("kein Auffangsegment")  # durch check() ausgeschlossen
