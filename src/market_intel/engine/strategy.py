"""Schritt 1: Suchstrategie aus Freitext und strukturierten Filtern.

Regelbasiert (Fachlexikon, Regionen, Zahlwörter, Absichten); die optionale KI darf nur
Begriffe ergänzen. Jeder Begriff trägt seine Herkunft (``filter``, ``rule``, ``llm``).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

from ..llm.assistant import Assistant
from ..usecases import UseCase

EUROPE = [
    "DE",
    "AT",
    "CH",
    "FR",
    "IT",
    "ES",
    "NL",
    "BE",
    "PL",
    "CZ",
    "DK",
    "SE",
    "GB",
    "IE",
    "PT",
    "FI",
    "NO",
    "LU",
    "HU",
    "SK",
    "SI",
]
_REGIONS: tuple[tuple[str, list[str]], ...] = (
    (r"\bdach\b|deutschland,? (ö|oe)sterreich (und|&) (der )?schweiz", ["DE", "AT", "CH"]),
    (r"\beuropa\b|\beurop(e|äisch|aeisch)|\beu\b", EUROPE),
    (r"deutschland|germany|deutsche[nrs]?\b|bundesweit", ["DE"]),
    (r"(ö|oe)sterreich|austria", ["AT"]),
    (r"schweiz|switzerland", ["CH"]),
    (r"frankreich|france", ["FR"]),
    (r"italien|italy", ["IT"]),
    (r"spanien|spain", ["ES"]),
    (r"niederlande|netherlands|holland", ["NL"]),
    (r"polen|poland", ["PL"]),
    (r"gro(ß|ss)britannien|vereinigtes k(ö|oe)nigreich|united kingdom|\buk\b|england", ["GB"]),
    (r"\busa\b|vereinigte staaten|united states|\bus\b", ["US"]),
)
_NUMBER_WORDS = {
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "ten": 10,
}
_INTENTS: tuple[tuple[str, str], ...] = (
    ("comparison", r"vergleich|compare|gegenüberstell"),
    ("company_development", r"entwicklung|historie|verlauf|development|letzten \S+ jahre"),
    (
        "new_customer_segments",
        r"au(ß|ss)erhalb der werbetechnik|neue (kunden|absatz)|plausiblen? "
        r"nutzen|potenzielle kunden|kundengruppen|bedarf",
    ),
    ("competitors", r"(ä|ae)hnliche[nr]? produkte|konkurr|wettbewerb|competitor"),
    ("processors", r"verarbeiten|verarbeitung|verarbeiter|anwender|nutzer"),
    (
        "market_leaders",
        r"gr(ö|oe)(ß|ss)te[nr]?|marktf(ü|ue)hrer|bedeutende[nr]?|f(ü|ue)hrende[nr]?|"
        r"largest|leading|top",
    ),
)
METRIC_WORDS = {
    "revenue": r"umsatz|revenue|erl(ö|oe)s",
    "headcount_total": r"mitarbeiter|besch(ä|ae)ftig|employees|headcount",
    "locations": r"standort|niederlassung|location",
    "hiring": r"stellen|jobs?|hiring",
    "markets": r"m(ä|ae)rkte|export|international",
}


@dataclass(slots=True)
class ResearchRequest:
    """Auftrag: Freitext plus strukturierte Filter (Filter haben Vorrang)."""

    task: str = ""
    countries: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    company_types: list[str] = field(default_factory=list)
    lists: list[str] = field(default_factory=list)
    years: int | None = None
    max_candidates: int = 25
    refresh: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchStrategy:
    """Ergebnis von Schritt 1."""

    task: str
    intents: list[str]
    countries: list[str]
    companies: list[str]
    name_terms: list[str]
    industry_terms: list[str]
    web_terms: list[str]
    use_cases: list[str]
    company_types: list[str]
    metrics: list[str]
    nace: list[str]
    lists: list[str]
    years: int
    max_candidates: int
    refresh: bool
    origin: dict[str, str] = field(default_factory=dict)
    concepts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchStrategy:
        return cls(**data)


def load_lexicon() -> list[dict[str, Any]]:
    raw = resources.files("market_intel.data").joinpath("lexicon.toml").read_text("utf-8")
    return list(tomllib.loads(raw).get("concept", []))


def _add(
    target: list[str], items: list[str] | tuple[str, ...], origin: dict[str, str], how: str
) -> None:
    for item in items:
        if item and item not in target:
            target.append(item)
            origin.setdefault(item, how)


def build_strategy(
    request: ResearchRequest, cases: dict[str, UseCase], assistant: Assistant | None = None
) -> SearchStrategy:
    """Freitext + Filter → Suchstrategie (deterministisch, optional KI-Ergänzung)."""
    text = request.task.lower()
    origin: dict[str, str] = {}
    countries: list[str] = []
    _add(countries, [c.upper() for c in request.countries], origin, "filter")
    if not countries:
        for pattern, codes in _REGIONS:
            if re.search(pattern, text):
                _add(countries, codes, origin, "rule")
                break
    if not countries:
        _add(countries, ["DE"], origin, "default")

    intents = [name for name, pattern in _INTENTS if re.search(pattern, text)]
    if request.companies and "comparison" not in intents and len(request.companies) > 1:
        intents.append("comparison")
    if not intents:
        intents = ["market_leaders"] if not request.companies else ["company_development"]

    companies: list[str] = []
    _add(companies, request.companies, origin, "filter")
    _add(companies, re.findall(r"[\"„“»]([^\"“”«]{2,80})[\"“”«]", request.task), origin, "rule")

    name_terms: list[str] = []
    industry_terms: list[str] = []
    web_terms: list[str] = []
    company_types: list[str] = []
    nace: list[str] = []
    concepts: list[str] = []
    _add(industry_terms, request.industries, origin, "filter")
    _add(name_terms, request.industries, origin, "filter")
    for concept in load_lexicon():
        if any(re.search(t, text) for t in concept.get("triggers", [])):
            concepts.append(str(concept["key"]))
            _add(name_terms, concept.get("name_terms", []), origin, "rule")
            _add(industry_terms, concept.get("industry_terms", []), origin, "rule")
            _add(web_terms, concept.get("web_terms", []), origin, "rule")
            _add(company_types, concept.get("company_types", []), origin, "rule")
            _add(nace, concept.get("nace", []), origin, "rule")
    _add(company_types, request.company_types, origin, "filter")

    use_cases: list[str] = []
    _add(use_cases, [u for u in request.use_cases if u in cases], origin, "filter")
    if not use_cases and set(intents) & {"new_customer_segments", "processors"}:
        exclude_sign = bool(re.search(r"au(ß|ss)erhalb der werbetechnik", text))
        keys = [k for k in cases if not (exclude_sign and k == "werbetechnik_beschriftung")]
        _add(use_cases, keys, origin, "rule")
    for key in use_cases:
        case = cases[key]
        _add(name_terms, case.name_terms, origin, f"use_case:{key}")
        _add(web_terms, case.website_terms, origin, f"use_case:{key}")
        _add(industry_terms, case.industry_terms, origin, f"use_case:{key}")

    metrics: list[str] = []
    _add(metrics, request.metrics, origin, "filter")
    _add(metrics, [m for m, p in METRIC_WORDS.items() if re.search(p, text)], origin, "rule")
    if not metrics:
        _add(metrics, ["revenue", "headcount_total"], origin, "default")

    years = request.years or 0
    if not years:
        m = re.search(r"letzten\s+(\d+|\w+)\s+jahr", text) or re.search(
            r"last\s+(\d+|\w+)\s+year", text
        )
        if m:
            token = m.group(1)
            years = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 0)
    years = years or 5

    if assistant is not None and assistant.available() and request.task:
        suggestion = assistant.suggest_strategy(request.task)
        _add(name_terms, suggestion.get("search_terms", [])[:5], origin, "llm")
        _add(web_terms, suggestion.get("search_terms", []), origin, "llm")
        _add(industry_terms, suggestion.get("industry_terms", [])[:5], origin, "llm")
        _add(companies, suggestion.get("company_names", []), origin, "llm")
        _add(metrics, suggestion.get("metrics", []), origin, "llm")

    return SearchStrategy(
        task=request.task,
        intents=intents,
        countries=countries,
        companies=companies,
        name_terms=name_terms,
        industry_terms=industry_terms,
        web_terms=web_terms,
        use_cases=use_cases,
        company_types=company_types,
        metrics=metrics,
        nace=nace,
        lists=list(request.lists),
        years=years,
        max_candidates=request.max_candidates,
        refresh=request.refresh,
        origin=origin,
        concepts=concepts,
    )
