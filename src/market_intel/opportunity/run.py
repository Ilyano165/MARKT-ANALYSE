"""Kandidaten bewerten: alle Anwendungsfälle je Unternehmen, Ergebnis speichern."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Engine, select

from .. import db, repo
from ..products import Catalog
from ..usecases import UseCase
from .assess import assess_company, store_assessment
from .evidence import EvidenceIndex
from .growth import assess_growth
from .model import Model
from .roles import classify_roles, load_roles

NO_FIT_KEY = "keiner"


def assess_all_applications(
    engine: Engine,
    company_id: int,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
) -> list[dict[str, Any]]:
    """Jeden Anwendungsfall prüfen; Ergebnisse mit Produkt-Fit zuerst, nach Priorität."""
    rules = load_roles()
    index = EvidenceIndex(engine, company_id, rules.statement)
    bp = model.section("business_potential")
    with engine.connect() as conn:
        growth = assess_growth(
            conn, company_id, bp["growth_thresholds"], int(bp["expansion_window_months"])
        )
        name = repo.company_row(conn, company_id)["display_name"]
    roles = classify_roles(index.texts, name, rules)  # Rollen gelten je Firma, nicht je Fall
    results = [
        assess_company(
            engine,
            company_id,
            case,
            catalog,
            model,
            index=index,
            growth=growth,
            roles=roles,
            rules=rules,
        )
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
    *,
    trigger: str = "bewertung",
) -> dict[int, dict[str, Any]]:
    """Speichert je Firma alle Anwendungsfälle mit Fit, sonst einen „kein Fit“-Eintrag.

    Je Firma entsteht eine Runde (``batch``) mit ihrer Grundlage (``basis``: Modell, Regeln,
    Datenstand) und ihrem Anlass (``trigger``). Die Runde wird in einer Transaktion
    geschrieben und nur, wenn keine Runde mit jüngerem Datenstand existiert – ein langsamer
    Lauf überschreibt nie ein neueres Ergebnis. Rückgabe: beste Bewertung je gespeicherter Firma.
    """
    from .current import basis_for, rules_digest

    digest = rules_digest(cases, catalog, model)
    best: dict[int, dict[str, Any]] = {}
    for company_id in company_ids:
        as_of = db.utcnow()  # Datenstand: vor dem Lesen der Firmendaten festgehalten
        basis = basis_for(engine, company_id, cases, catalog, model, digest=digest)
        results = assess_all_applications(engine, company_id, cases, catalog, model)
        with_fit = [r for r in results if r["scores"]["product_relevance"] > 0]
        stored = with_fit or [no_fit_summary(results, len(cases))]
        # Eine Runde je Firma: nur sie gilt als aktuell (latest_assessments). Sonst blieben
        # Anwendungsfälle, die nach einer Modelländerung keinen Fit mehr haben, mit ihrer
        # alten Bewertung „aktuell“ (z. B. v2-Fit aus Menüpunkten).
        batch = uuid.uuid4().hex
        for result in stored:
            result["batch"] = batch
            result["basis"] = basis
            result["trigger"] = trigger
        with engine.begin() as conn:
            newer = conn.execute(
                select(db.opportunity_score.c.id).where(
                    db.opportunity_score.c.company_id == company_id,
                    db.opportunity_score.c.assessed_at > as_of,
                )
            ).first()
            if newer is not None:
                continue
            for result in stored:
                store_assessment(conn, result, run_id, assessed_at=as_of)
        best[company_id] = stored[0]
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


def no_fit_summary(results: list[dict[str, Any]], n_cases: int) -> dict[str, Any]:
    """Zusammenfassung, wenn zu keinem Anwendungsfall eine Tätigkeit belegt ist."""
    summary = dict(results[0])
    summary["use_case"] = NO_FIT_KEY
    summary["use_case_name"] = "kein Anwendungsfall belegt"
    hypothesis = dict(summary["hypothesis"])
    data = summary.get("assessment", {}).get("data_status", {})
    if summary.get("assessment", {}).get("fit_status") == "keine_daten":
        hypothesis["statement"] = (
            f"Für {summary['company']} liegen keine eigenen Unternehmensangaben vor "
            f"({data.get('label', 'unbekannt')}). Ob eine Tätigkeit zu einem der {n_cases} "
            "Anwendungsfälle passt, ist unbekannt – nicht belegt, aber auch nicht ausgeschlossen."
        )
    else:
        hypothesis["statement"] = (
            f"Für {summary['company']} ist zu keinem der {n_cases} Anwendungsfälle der "
            "Produktmatrix eine Tätigkeit belegt. Eine Bedarfshypothese wird daher nicht "
            "aufgestellt."
        )
    hypothesis["products"] = []
    summary["hypothesis"] = hypothesis
    return summary
