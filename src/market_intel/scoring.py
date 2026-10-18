"""Transparente Chancenbewertung (Scoring v1, Entwurf) – jede Zahl ist zerlegbar.

Fehlende Indikatoren werden weder mit 0 noch mit Schätzwerten gefüllt: Der Score wird über
die vorhandenen Gewichte renormiert und die Datenvollständigkeit ausgewiesen.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, insert, select

from . import db
from .analytics import growth, series_for

MODEL_VERSION = "v1-entwurf"
WEIGHTS = {"fit": 0.35, "size": 0.20, "growth": 0.20, "signals": 0.25}
FIT_SCORE = {"strong": 1.0, "plausible": 0.6, "weak": 0.2}
MIN_COMPLETENESS = 50


def _best_fit(conn: Connection, company_id: int, use_case_key: str) -> tuple[str | None, str]:
    rows = conn.execute(
        select(db.company_product_fit.c.fit_level, db.company_product_fit.c.rationale).where(
            db.company_product_fit.c.company_id == company_id,
            db.company_product_fit.c.use_case_key == use_case_key,
        )
    ).all()
    if not rows:
        return None, ""
    best = max(rows, key=lambda r: FIT_SCORE.get(r.fit_level, 0))
    return best.fit_level, best.rationale


def assess(conn: Connection, company_id: int, use_case_key: str) -> dict[str, Any]:
    """Komponenten berechnen (ohne zu speichern)."""
    components: dict[str, dict[str, Any]] = {}
    fit, rationale = _best_fit(conn, company_id, use_case_key)
    if fit in FIT_SCORE:
        components["fit"] = {"raw": fit, "score": FIT_SCORE[fit], "basis": rationale}

    heads = series_for(conn, company_id, "employee", "headcount_total")
    if heads:
        value = float(heads[-1]["value"])
        components["size"] = {
            "raw": value,
            "score": round(min(1.0, math.log10(max(value, 1)) / 4), 3),
            "basis": f"Mitarbeiter {value:.0f} (log10/4, max 10.000 = 1)",
        }

    for kind, metric in (("employee", "headcount_total"), ("financial", "revenue")):
        result = growth(series_for(conn, company_id, kind, metric))
        if result.cagr_pct is not None:
            cagr = float(result.cagr_pct)
            components["growth"] = {
                "raw": cagr,
                "score": round(min(1.0, max(0.0, (cagr + 10) / 30)), 3),
                "basis": f"CAGR {metric} {cagr:+.1f} % "
                f"({result.first_period} → {result.last_period}); "
                "-10 % = 0, +20 % = 1",
            }
            break

    since = date.today() - timedelta(days=365)
    signals = conn.execute(
        select(db.business_signal.c.id).where(
            db.business_signal.c.company_id == company_id, db.business_signal.c.event_date >= since
        )
    ).all()
    if signals:
        components["signals"] = {
            "raw": len(signals),
            "score": round(min(1.0, len(signals) / 3), 3),
            "basis": f"{len(signals)} Signale in 12 Monaten (3 = Maximum)",
        }

    available = sum(WEIGHTS[k] for k in components)
    completeness = round(100 * available / sum(WEIGHTS.values()))
    total = (
        sum(WEIGHTS[k] * c["score"] for k, c in components.items()) / available * 100
        if available
        else 0.0
    )
    for key, comp in components.items():
        comp["weight"] = WEIGHTS[key]
        comp["contribution"] = round(WEIGHTS[key] * comp["score"] / available * 100, 1)
    if completeness < MIN_COMPLETENESS or "fit" not in components:
        priority = "insufficient_data"
    elif total >= 65:
        priority = "A"
    elif total >= 40:
        priority = "B"
    else:
        priority = "C"
    return {
        "total": round(total, 1),
        "components": components,
        "completeness": completeness,
        "priority": priority,
        "missing": [k for k in WEIGHTS if k not in components],
    }


def score_use_case(engine: Engine, use_case_key: str, run_id: int | None) -> int:
    """Alle Firmen mit Fit für den Anwendungsfall bewerten und speichern."""
    with engine.begin() as conn:
        company_ids = sorted(
            {
                r.company_id
                for r in conn.execute(
                    select(db.company_product_fit.c.company_id).where(
                        db.company_product_fit.c.use_case_key == use_case_key
                    )
                )
            }
        )
        for company_id in company_ids:
            result = assess(conn, company_id, use_case_key)
            conn.execute(
                insert(db.opportunity_assessment).values(
                    company_id=company_id,
                    use_case_key=use_case_key,
                    scoring_model_version=MODEL_VERSION,
                    total_score=Decimal(str(result["total"])),
                    components=json.dumps(
                        {
                            "components": result["components"],
                            "missing": result["missing"],
                            "weights": WEIGHTS,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    data_completeness_pct=result["completeness"],
                    priority=result["priority"],
                    assessed_at=db.utcnow(),
                    research_run_id=run_id,
                )
            )
    return len(company_ids)
