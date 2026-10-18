"""Zeitreihen, Wachstum, Vergleich und Datenlücken – vollständig lokal berechnet.

Wachstum wird nur zwischen vergleichbaren Werten berechnet (gleiche Einheit/Währung,
gleicher Konsolidierungskreis, gleiche Zeitraumart, bei Geschäftsjahren ähnliche Länge).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any

from sqlalchemy import Connection, func, select

from . import db, repo

METRICS = (("financial", "revenue"), ("employee", "headcount_total"))


@dataclass(frozen=True, slots=True)
class Growth:
    """Wachstumskennzahlen einer Reihe."""

    points: int
    first_period: str | None
    last_period: str | None
    cagr_pct: Decimal | None
    yoy: tuple[tuple[str, Decimal], ...]
    mixed_provenance: bool
    note: str


def _comparable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if (a["unit"], a["currency"], a["consolidation_scope"], a["period_type"]) != (
        b["unit"],
        b["currency"],
        b["consolidation_scope"],
        b["period_type"],
    ):
        return False
    if a["period_type"] == "fiscal_year" and a["period_start"] and b["period_start"]:
        len_a = (a["period_end"] - a["period_start"]).days
        len_b = (b["period_end"] - b["period_start"]).days
        return bool(abs(len_a - len_b) <= 20)
    return True


def growth(series: list[dict[str, Any]]) -> Growth:
    """CAGR zwischen erstem und letztem vergleichbaren Wert, dazu Vorjahresänderungen."""
    if len(series) < 2:
        return Growth(
            len(series),
            None,
            None,
            None,
            (),
            False,
            "weniger als zwei Zeitpunkte – keine Entwicklung berechenbar",
        )
    last = series[-1]
    comparable = [r for r in series if _comparable(r, last)]
    if len(comparable) < 2:
        return Growth(
            len(series),
            None,
            None,
            None,
            (),
            False,
            "Werte nicht vergleichbar (Einheit, Scope oder Zeitraum verschieden)",
        )
    yoy = []
    for prev, cur in pairwise(comparable):
        years = (cur["period_end"] - prev["period_end"]).days / 365.25
        if 0.8 <= years <= 1.2 and Decimal(prev["value"]) > 0:
            change = (Decimal(cur["value"]) / Decimal(prev["value"]) - 1) * 100
            yoy.append((cur["period_end"].isoformat(), change.quantize(Decimal("0.1"))))
    first = comparable[0]
    years = Decimal((last["period_end"] - first["period_end"]).days) / Decimal("365.25")
    cagr = None
    note = ""
    if years >= Decimal("0.9") and Decimal(first["value"]) > 0:
        ratio = float(Decimal(last["value"]) / Decimal(first["value"]))
        cagr = Decimal(str((ratio ** (1 / float(years)) - 1) * 100)).quantize(Decimal("0.1"))
    else:
        note = "Zeitspanne unter einem Jahr"
    mixed = len({r["provenance_class"] for r in comparable}) > 1
    if mixed:
        note = (note + "; " if note else "") + "gemischte Herkunftsklassen"
    return Growth(
        len(comparable),
        first["period_end"].isoformat(),
        last["period_end"].isoformat(),
        cagr,
        tuple(yoy),
        mixed,
        note,
    )


def series_for(
    conn: Connection, company_id: int, kind: str, metric: str, currency: str | None = None
) -> list[dict[str, Any]]:
    """Gültige Werte je Zeitraum; optional auf eine Währung beschränkt."""
    rows = repo.current_values(conn, company_id, kind, metric)
    if currency:
        rows = [r for r in rows if r["currency"] == currency]
    return rows


def _primary_revenue_series(conn: Connection, company_id: int) -> list[dict[str, Any]]:
    rows = repo.current_values(conn, company_id, "financial", "revenue")
    eur = [r for r in rows if r["currency"] == "EUR"]
    if eur:
        return eur
    currencies = {r["currency"] for r in rows}
    if not currencies:
        return []
    main = max(currencies, key=lambda c: sum(1 for r in rows if r["currency"] == c))
    return [r for r in rows if r["currency"] == main]


def compare(conn: Connection, company_ids: list[int]) -> list[dict[str, Any]]:
    """Vergleichstabelle: letzter gültiger Wert, Entwicklung, Status, Quellen, Lücken."""
    table = []
    for company_id in company_ids:
        company = repo.company_row(conn, company_id)
        ids = repo.identifiers_of(conn, company_id)
        revenue = _primary_revenue_series(conn, company_id)
        heads = repo.current_values(conn, company_id, "employee", "headcount_total")
        rev_growth, head_growth = growth(revenue), growth(heads)
        gaps = repo.gaps_of(conn, company_id)
        n_obs = sum(
            conn.execute(
                select(func.count()).select_from(t).where(t.c.company_id == company_id)
            ).scalar()
            or 0
            for t in db.OBSERVATION_TABLES.values()
        )
        sources = sorted(
            {r["source_key"] for k, _ in METRICS for r in repo.observations(conn, company_id, k)}
        )
        latest_rev = revenue[-1] if revenue else None
        latest_head = heads[-1] if heads else None
        table.append(
            {
                "company_id": company_id,
                "name": company["display_name"],
                "country": company["country_code"],
                "lei": ids.get("lei"),
                "wikidata": ids.get("wikidata"),
                "revenue_latest": latest_rev["value"] if latest_rev else None,
                "revenue_currency": latest_rev["currency"] if latest_rev else None,
                "revenue_period_end": latest_rev["period_end"] if latest_rev else None,
                "revenue_status": _status(latest_rev),
                "revenue_cagr_pct": rev_growth.cagr_pct,
                "revenue_cagr_span": _span(rev_growth),
                "revenue_note": rev_growth.note,
                "headcount_latest": latest_head["value"] if latest_head else None,
                "headcount_date": latest_head["period_end"] if latest_head else None,
                "headcount_status": _status(latest_head),
                "headcount_cagr_pct": head_growth.cagr_pct,
                "headcount_cagr_span": _span(head_growth),
                "headcount_note": head_growth.note,
                "observations": n_obs,
                "sources": ", ".join(sources),
                "gaps": "; ".join(
                    sorted(
                        {
                            f"{g['metric']} ({g['reason']})"
                            for g in gaps
                            if not _has_metric(conn, company_id, g["metric"])
                        }
                    )
                ),
            }
        )
    return table


def _has_metric(conn: Connection, company_id: int, metric: str) -> bool:
    return any(
        repo.current_values(conn, company_id, kind, metric) for kind, m in METRICS if m == metric
    )


def _status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "unbekannt"
    status = f"{row['verification_status']} / {row['provenance_class']}"
    return status + (" / WIDERSPRUCH" if row.get("conflict") else "")


def _span(item: Growth) -> str:
    return f"{item.first_period} → {item.last_period}" if item.cagr_pct is not None else ""


def data_quality(conn: Connection, company_ids: list[int]) -> dict[str, int]:
    """Zähler je Status über alle Beobachtungen und Lücken."""
    counts: dict[str, int] = {}
    for table in db.OBSERVATION_TABLES.values():
        rows = conn.execute(
            select(table.c.verification_status, func.count())
            .where(table.c.company_id.in_(company_ids))
            .group_by(table.c.verification_status)
        ).all()
        for status, n in rows:
            counts[status] = counts.get(status, 0) + int(n)
    gaps = conn.execute(
        select(func.count())
        .select_from(db.data_gap)
        .where(db.data_gap.c.company_id.in_(company_ids))
    ).scalar()
    counts["gaps_documented"] = int(gaps or 0)
    return counts
