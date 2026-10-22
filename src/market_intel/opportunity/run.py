"""Kandidaten bewerten: alle Anwendungsfälle je Unternehmen, Ergebnis speichern."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select

from .. import db
from ..products import Catalog
from ..usecases import UseCase
from .assess import assess_company, store_assessment
from .evidence import EvidenceIndex
from .growth import assess_growth
from .model import Model

NO_FIT_KEY = "keiner"


def assess_all_applications(
    engine: Engine,
    company_id: int,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
) -> list[dict[str, Any]]:
    """Jeden Anwendungsfall prüfen; Ergebnisse mit Produkt-Fit zuerst, nach Priorität."""
    index = EvidenceIndex(engine, company_id)
    bp = model.section("business_potential")
    with engine.connect() as conn:
        growth = assess_growth(
            conn, company_id, bp["growth_thresholds"], int(bp["expansion_window_months"])
        )
    results = [
        assess_company(engine, company_id, case, catalog, model, index=index, growth=growth)
        for case in cases.values()
    ]
    results.sort(
        key=lambda r: (-r["scores"]["priority"], -r["scores"]["product_relevance"], r["use_case"])
    )
    return results


def assess_and_store(
    engine: Engine,
    company_ids: list[int],
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
    run_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Speichert je Firma alle Anwendungsfälle mit Fit, sonst einen „kein Fit“-Eintrag."""
    best: dict[int, dict[str, Any]] = {}
    for company_id in company_ids:
        results = assess_all_applications(engine, company_id, cases, catalog, model)
        with_fit = [r for r in results if r["scores"]["product_relevance"] > 0]
        if with_fit:
            for result in with_fit:
                store_assessment(engine, result, run_id)
            best[company_id] = with_fit[0]
        else:
            summary = dict(results[0])
            summary["use_case"] = NO_FIT_KEY
            summary["use_case_name"] = "kein Anwendungsfall belegt"
            store_assessment(engine, summary, run_id)
            best[company_id] = summary
    return best


def all_company_ids(engine: Engine) -> list[int]:
    """Alle nicht zusammengeführten Unternehmen."""
    with engine.connect() as conn:
        return [
            int(i)
            for i in conn.execute(
                select(db.company.c.id).where(db.company.c.merged_into_id.is_(None))
            ).scalars()
        ]
