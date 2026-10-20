"""Zeitreihen aus der Datenbank: Umsatz, Mitarbeiter, Segmente, Personal-Dynamik."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from .. import db, repo
from ..domain import HEADCOUNT_METRICS, METRIC_LABELS
from .timeseries import (
    FLOW,
    STOCK,
    Analysis,
    Event,
    Row,
    analyze,
    as_dict,
    point_from_row,
    split_series,
)

SCOPE_LABEL = {
    "group": "Konzern",
    "single_entity": "Einzelgesellschaft",
    "unknown": "Kreis unbekannt",
}


def structural_events(conn: Connection, company_id: int) -> list[Event]:
    """Ereignisse, die Zeitreihen beeinflussen können (mit Zuverlässigkeit)."""
    table = db.corporate_event
    rows = conn.execute(
        select(table).where(
            table.c.company_id == company_id, table.c.event_type.in_(repo.STRUCTURAL_EVENTS)
        )
    ).mappings()
    return [
        Event(
            int(r["id"]),
            r["event_type"],
            r["event_date"],
            r["summary"],
            r["confidence"],
            r["verification_status"],
        )
        for r in rows
    ]


def gap_reason(conn: Connection, company_id: int, metric: str) -> str:
    """Begründung für fehlende Jahre aus den dokumentierten Lücken."""
    gaps = [g for g in repo.gaps_of(conn, company_id) if g["metric"] == metric]
    if not gaps:
        return "kein Wert in den Quellen gefunden"
    latest = max(gaps, key=lambda g: g["checked_at"])
    return (
        f"gesucht in {latest['searched_sources'] or 'unbekannt'} am "
        f"{latest['checked_at']:%d.%m.%Y}: {latest['reason']}"
    )


def _title(metric: str, unit: str, scope: str, extra: str = "") -> str:
    base = f"{METRIC_LABELS.get(metric, metric)} ({unit}, {SCOPE_LABEL.get(scope, scope)})"
    return base + (f" – {extra}" if extra else "")


def _pack(analysis: Analysis, title: str, **extra: Any) -> dict[str, Any]:
    data = as_dict(analysis)
    data["title"] = title
    data.update(extra)
    return data


def revenue_series(
    conn: Connection,
    company_id: int,
    *,
    years: int = 10,
    include_estimates: bool = False,
    segment: str = "",
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Umsatzreihen in Originalwährung je Scope; zusätzlich EUR-Umrechnung, falls vorhanden."""
    rows = repo.current_values(conn, company_id, "financial", "revenue", segment)
    events = structural_events(conn, company_id)
    reason = gap_reason(conn, company_id, "revenue")
    native = [point_from_row(r, "financial") for r in rows if r["provenance_class"] != "derived"]
    out = []
    for group in split_series(native):
        analysis = analyze(
            group,
            metric="revenue",
            mode=FLOW,
            events=events,
            years=years,
            include_estimates=include_estimates,
            gap_default=reason,
            as_of=as_of,
        )
        out.append(
            _pack(
                analysis,
                _title("revenue", analysis.unit, analysis.scope, "Originalwährung"),
                currency_mode="native",
                segment=segment,
            )
        )
    foreign = {p.currency for p in native if p.currency != "EUR"}
    derived = [r for r in rows if r["provenance_class"] == "derived" and r["currency"] == "EUR"]
    if foreign and derived:
        by_key: dict[tuple[date, str], dict[str, Any]] = {}
        for r in rows:
            if r["currency"] != "EUR":
                continue
            key = (r["period_end"], r["consolidation_scope"])
            if key not in by_key or by_key[key]["provenance_class"] == "derived":
                by_key[key] = r
        converted = [point_from_row(r, "financial") for r in by_key.values()]
        for group in split_series(converted):
            analysis = analyze(
                group,
                metric="revenue",
                mode=FLOW,
                events=events,
                years=years,
                include_estimates=include_estimates,
                as_of=as_of,
            )
            analysis.notes.insert(
                0,
                "Umgerechnet mit EZB-Jahresdurchschnittskursen: Veränderungen enthalten "
                "Wechselkurseffekte; eine währungsbereinigte (organische) Rate wird nicht "
                "berechnet",
            )
            out.append(
                _pack(
                    analysis,
                    _title("revenue", "EUR", analysis.scope, "umgerechnet"),
                    currency_mode="converted",
                    segment=segment,
                )
            )
    return out


def headcount_series(
    conn: Connection,
    company_id: int,
    *,
    years: int = 10,
    include_estimates: bool = False,
    segment: str = "",
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Mitarbeiterreihen je Messgröße (Stichtag, Jahresdurchschnitt, VZÄ) – nie vermischt."""
    events = structural_events(conn, company_id)
    out = []
    for metric in HEADCOUNT_METRICS:
        rows = repo.current_values(conn, company_id, "employee", metric, segment)
        if not rows:
            continue
        reason = gap_reason(conn, company_id, metric)
        points = [point_from_row(r, "employee") for r in rows]
        mode = FLOW if metric == "headcount_avg" else STOCK
        for group in split_series(points):
            analysis = analyze(
                group,
                metric=metric,
                mode=mode,
                events=events,
                years=years,
                include_estimates=include_estimates,
                gap_default=reason,
                as_of=as_of,
            )
            out.append(
                _pack(
                    analysis,
                    _title(metric, "Personen", analysis.scope),
                    segment=segment,
                    dynamics=workforce_dynamics(conn, company_id, analysis),
                )
            )
    return out


def segments_of(conn: Connection, company_id: int) -> dict[str, list[str]]:
    """Vorhandene Segmente (z. B. Länder, Standorte) je Beobachtungsart."""
    result: dict[str, list[str]] = {}
    for kind, table in db.OBSERVATION_TABLES.items():
        values = conn.execute(
            select(table.c.segment)
            .where(table.c.company_id == company_id, table.c.segment != "")
            .distinct()
        ).scalars()
        result[kind] = sorted(values)
    return result


def workforce_dynamics(
    conn: Connection, company_id: int, analysis: Analysis
) -> list[dict[str, Any]]:
    """Nettoveränderung je Jahr neben belegten Einstellungs-/Abbauangaben und Stellenanzeigen.

    Grundsatz: Eine Nettoveränderung (1.000 → 1.100) ist **keine** Zahl von Einstellungen
    (es können 300 eingestellt und 200 ausgeschieden sein). Stellenanzeigen zeigen Bedarf,
    keine Einstellungen. Beides wird deshalb nur nebeneinander gezeigt, nie verrechnet.
    """
    events = [
        e
        for e in repo.events_of(conn, company_id)
        if e["event_type"] in ("layoff", "hiring", "restructuring", "site_closure", "new_site")
    ]
    ads = conn.execute(
        select(db.hiring_observation).where(db.hiring_observation.c.company_id == company_id)
    ).mappings()
    ads_by_year: dict[int, list[dict[str, Any]]] = {}
    for ad in ads:
        ads_by_year.setdefault(ad["observation_date"].year, []).append(dict(ad))
    rows: list[Any] = list(analysis.rows)
    known_years = {r.year for r in rows}
    extra_years = {e["event_date"].year for e in events if e["event_date"]} | set(ads_by_year)
    first_year = rows[0].year if rows else min(extra_years, default=0)
    for year in sorted(y for y in extra_years - known_years if y >= first_year):
        rows.append(Row(str(year), year, None, status="no_value"))
    rows.sort(key=lambda r: r.year)
    out = []
    for row in rows:
        year_events = [e for e in events if e["event_date"] and e["event_date"].year == row.year]
        layoffs = [e for e in year_events if e["event_type"] == "layoff"]
        hires = [e for e in year_events if e["event_type"] == "hiring"]
        laid = sum(e["affected_headcount"] or 0 for e in layoffs if e["affected_headcount"])
        hired = sum(e["affected_headcount"] or 0 for e in hires if e["affected_headcount"])
        year_ads = ads_by_year.get(row.year, [])
        statement = _statement(
            row.change_abs, row.since_abs, row.since_label, laid, hired, row.point, year_ads
        )
        out.append(
            {
                "label": row.label,
                "net_change": None if row.change_abs is None else format(row.change_abs, "f"),
                "documented_layoffs": laid or None,
                "documented_hiring": hired or None,
                "events": [
                    {
                        "id": e["id"],
                        "type": e["event_type"],
                        "date": e["event_date"].isoformat(),
                        "summary": e["summary"],
                        "affected_headcount": e["affected_headcount"],
                        "verification": e["verification_status"],
                        "source_url": e["source_url"],
                    }
                    for e in year_events
                ],
                "job_ads": [
                    {
                        "date": a["observation_date"].isoformat(),
                        "open_positions": a["open_positions_count"],
                        "locations": json.loads(a["locations"] or "[]"),
                        "evidence_id": a["evidence_id"],
                    }
                    for a in year_ads
                ],
                "statement": statement,
            }
        )
    return out


def _statement(
    net: Decimal | None,
    since: Decimal | None,
    since_label: str | None,
    laid: int,
    hired: int,
    point: Any,
    ads: list[dict[str, Any]] | None = None,
) -> str:
    parts = []
    if point is None:
        parts.append("Mitarbeiterzahl unbekannt – keine Aussage zu Wachstum oder Abbau")
    elif net is not None:
        sign = "+" if net > 0 else ""
        parts.append(
            f"Nettoveränderung {sign}{net:,.0f}".replace(",", ".")
            + " – daraus lassen sich Einstellungen und Abgänge nicht ableiten"
        )
    elif since is not None:
        parts.append(
            f"Nettoveränderung seit {since_label}: {since:+,.0f}".replace(",", ".")
            + " (Zwischenjahre unbekannt)"
        )
    if laid:
        parts.append(f"angekündigter/berichteter Abbau: {laid} Stellen (laut Quelle)")
    if hired:
        parts.append(f"angekündigte/berichtete Einstellungen: {hired} (laut Quelle)")
    for ad in ads or []:
        parts.append(
            f"{ad['open_positions_count']} Stellenanzeige(n) am "
            f"{ad['observation_date']:%d.%m.%Y} – Personalbedarf, keine Einstellungen"
        )
    return "; ".join(parts)


def latest_figure(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Jüngster belegter Wert über alle Reihen (bevorzugt Originalwährung, Konzern)."""
    best = None
    for item in series:
        if item.get("currency_mode") == "converted":
            continue
        for row in item["rows"]:
            point = row["point"]
            if point is None:
                continue
            rank = (point["period_end"], point["scope"] == "group")
            if best is None or rank > best[0]:
                best = (rank, {**point, "label": row["label"], "series": item["title"]})
    return best[1] if best else None
