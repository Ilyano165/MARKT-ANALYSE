"""Zeitreihen, Wachstum, Vergleich und Datenlücken – vollständig lokal berechnet.

Wachstum wird nur zwischen vergleichbaren Werten berechnet (gleiche Einheit/Währung,
gleicher Konsolidierungskreis, gleiche Zeitraumart, bei Geschäftsjahren ähnliche Länge).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from . import db, repo
from .intel.series import structural_events
from .intel.timeseries import FLOW, STOCK, Event, Point, analyze, split_series

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


def _point(row: dict[str, Any]) -> Point:
    unit = row["unit"]
    return Point(
        period_end=row["period_end"],
        period_start=row.get("period_start"),
        value=Decimal(row["value"]),
        unit=unit,
        currency=row.get("currency"),
        scope=row.get("consolidation_scope", "unknown"),
        period_type=row.get("period_type", "fiscal_year"),
        provenance=row.get("provenance_class", "company_reported"),
        verification=row.get("verification_status", "reported"),
        confidence=row.get("confidence", "medium"),
        conflict=bool(row.get("conflict")),
        observation_id=int(row.get("id") or 0),
        kind="employee" if unit == "persons" else "financial",
        source_key=row.get("source_key", "") or "",
        limitations=row.get("limitations", "") or "",
        methodology=row.get("methodology", "") or "",
    )


def growth(series: list[dict[str, Any]], events: list[Event] | None = None) -> Growth:
    """CAGR und Vorjahresveränderungen nach den Regeln von ``intel.timeseries`` (§7a).

    Gleiches Regelwerk wie Profil und Oberfläche: Brüche (Währung, Scope, Geschäftsjahr,
    Rumpfjahr) beenden einen Abschnitt; maßgeblich ist der Abschnitt mit dem jüngsten Wert.
    Schätzungen werden nicht einbezogen.
    """
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
    points = [_point(r) for r in series]
    groups = split_series(points)
    group = max(groups, key=lambda g: max(p.period_end for p in g))
    mode = STOCK if group[-1].period_type == "point_in_time" else FLOW
    analysis = analyze(group, metric="", mode=mode, events=events or [], years=100)
    yoy = tuple(
        (r.point.period_end.isoformat(), r.change_pct)
        for r in analysis.rows
        if r.point is not None and r.change_pct is not None
    )
    total = analysis.total
    if total is None or total.cagr_pct is None:
        reason = (
            "; ".join(analysis.notes + (total.notes if total else []))
            or "Werte nicht vergleichbar (Einheit, Scope oder Zeitraum verschieden)"
        )
        return Growth(len(analysis.known_points), None, None, None, yoy, False, reason)
    by_label = {r.label: r.point for r in analysis.rows if r.point is not None}
    first, last = by_label[total.start_label], by_label[total.end_label]
    assert first is not None and last is not None
    span_points = [
        r.point
        for r in analysis.rows
        if r.point is not None and first.period_end <= r.point.period_end <= last.period_end
    ]
    mixed = len({p.provenance for p in span_points}) > 1
    return Growth(
        len(span_points),
        first.period_end.isoformat(),
        last.period_end.isoformat(),
        total.cagr_pct,
        yoy,
        mixed,
        "; ".join(total.notes),
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
        events = structural_events(conn, company_id)
        rev_growth, head_growth = growth(revenue, events), growth(heads, events)
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
