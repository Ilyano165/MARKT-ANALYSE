"""SEC EDGAR: Jahresumsätze und (falls gemeldet) Mitarbeiter aus XBRL ``companyfacts``.

Amtliche Pflichtveröffentlichung → ``official``. Zuordnung einer Firma zu einer CIK nur über
starke Identifikatoren (z. B. Wikidata P5531) – reine Namensgleichheit wird als Kandidat
gemeldet, nicht übernommen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .. import repo
from ..context import Ctx
from ..domain import (
    Confidence,
    EvidenceDraft,
    GapReason,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from ..resolution import name_similarity
from .common import as_dict, dict_items, json_object

SOURCE = "sec_edgar"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
VERSION = "sec-xbrl-0.1.0"
REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


@dataclass(frozen=True, slots=True)
class CikCandidate:
    """Möglicher CIK-Treffer über den Namen (nur zur Prüfung)."""

    cik: int
    title: str
    ticker: str
    similarity: float


def name_candidates(ctx: Ctx, name: str, threshold: float = 0.9) -> list[CikCandidate]:
    """CIK-Kandidaten nach Namensähnlichkeit aus der offiziellen Tickerliste."""
    result = ctx.gateway.get(SOURCE, TICKERS, purpose="SEC-Tickerliste", title="company_tickers")
    if not result.ok:
        ctx.note(f"SEC-Tickerliste nicht abrufbar: {result.status.value} {result.error}")
        return []
    candidates = []
    for entry in (e for e in json_object(ctx, result).values() if isinstance(e, dict)):
        similarity = name_similarity(name, str(entry.get("title", "")))
        if similarity >= threshold:
            candidates.append(
                CikCandidate(
                    int(entry["cik_str"]),
                    str(entry["title"]),
                    str(entry.get("ticker", "")),
                    similarity,
                )
            )
    return sorted(candidates, key=lambda c: -c.similarity)[:5]


def _annual_entries(units: Any) -> list[tuple[str, dict[str, Any]]]:
    picked: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
    for unit, entries in as_dict(units).items():
        for entry in dict_items(entries):
            if entry.get("form") not in ANNUAL_FORMS or entry.get("fp") != "FY":
                continue
            start, end = entry.get("start"), entry.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                continue
            if isinstance(entry.get("val"), bool) or not isinstance(entry.get("val"), int | float):
                continue
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if not 340 <= days <= 380:
                continue
            key = (unit, start, end)
            if key not in picked or entry.get("filed", "") > picked[key][1].get("filed", ""):
                picked[key] = (unit, entry)
    return list(picked.values())


def ingest_facts(ctx: Ctx, company_id: int, cik: int) -> int:
    """Umsatzreihe (und Mitarbeiter, falls im XBRL) speichern. Gibt Anzahl neuer Werte zurück."""
    result = ctx.gateway.get(
        SOURCE,
        FACTS.format(cik=cik),
        purpose=f"XBRL companyfacts {cik}",
        title=f"SEC companyfacts CIK {cik}",
    )
    if not result.ok:
        with ctx.engine.begin() as conn:
            repo.add_gap(
                conn,
                company_id,
                "revenue",
                [SOURCE],
                GapReason.SOURCE_UNAVAILABLE,
                f"{result.status.value} {result.error}",
                ctx.run_id,
            )
        return 0
    assert result.document_id is not None
    raw = result.text
    facts = as_dict(json_object(ctx, result).get("facts"))
    gaap = as_dict(facts.get("us-gaap"))
    drafts: list[ObservationDraft] = []
    used_periods: set[tuple[str, str]] = set()
    for concept in REVENUE_CONCEPTS:
        for unit, entry in _annual_entries(as_dict(gaap.get(concept)).get("units")):
            if not re.fullmatch(r"[A-Z]{3}", unit) or (entry["end"], unit) in used_periods:
                continue
            used_periods.add((entry["end"], unit))
            drafts.append(
                _draft(
                    company_id,
                    result.document_id,
                    raw,
                    "financial",
                    "revenue",
                    concept,
                    unit,
                    entry,
                )
            )
    for unit, entry in _annual_entries(
        as_dict(as_dict(facts.get("dei")).get("EntityNumberOfEmployees")).get("units")
    ):
        drafts.append(
            _draft(
                company_id,
                result.document_id,
                raw,
                "employee",
                "headcount_total",
                "EntityNumberOfEmployees",
                unit,
                entry,
            )
        )
    created = 0
    with ctx.engine.begin() as conn:
        for draft in drafts:
            if (
                draft.evidence.quote_verified
                and repo.ingest_observation(conn, draft, ctx.run_id).created
            ):
                created += 1
    return created


def _draft(
    company_id: int,
    document_id: int,
    raw: str,
    kind: str,
    metric: str,
    concept: str,
    unit: str,
    entry: dict[str, Any],
) -> ObservationDraft:
    value = Decimal(str(entry["val"]))
    quote = f'"val":{entry["val"]}'
    verified = quote in raw.replace('"val": ', '"val":')
    return ObservationDraft(
        company_id=company_id,
        kind=kind,
        metric=metric,
        value=value,
        unit=unit if kind == "financial" else "persons",
        currency=unit if kind == "financial" else None,
        period_type=PeriodType.FISCAL_YEAR,
        period_start=date.fromisoformat(entry["start"]),
        period_end=date.fromisoformat(entry["end"]),
        scope=Scope.GROUP,
        provenance_class=ProvenanceClass.OFFICIAL,
        confidence=Confidence.HIGH,
        evidence=EvidenceDraft(
            document_id,
            f"facts.{concept}[accn={entry.get('accn')},end={entry['end']}]",
            quote,
            "xbrl",
            VERSION,
            verified,
        ),
        limitations=f"Formular {entry.get('form')}, eingereicht {entry.get('filed')}",
    )
