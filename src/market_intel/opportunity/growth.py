"""Wachstum und Geschäftsentwicklung eines Kandidaten – mit Belegstufe je Signal.

Belegstufen:
* ``confirmed``   – bestätigtes Ereignis (amtlich, manuell geprüft oder Unternehmensmeldung mit
  hohem/mittlerem Vertrauen)
* ``derived``     – aus belegten Zahlen berechnet (Umsatz-/Mitarbeiterentwicklung, Stellenanzeigen
  als Bedarfsindikator, neu belegte Standorte/Länder)
* ``unconfirmed`` – Regel-Treffer in Pressetexten, nicht geprüft (bloße Vermutung)

Fehlende Daten ergeben „unknown“, nie eine schlechte Bewertung.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from .. import db, repo
from ..intel.series import headcount_series, revenue_series

EVENT_LABELS = {
    "hiring": "Einstellungen",
    "layoff": "Personalabbau",
    "new_site": "neuer Standort",
    "site_closure": "Standortschließung",
    "new_market": "neuer Markt/Land",
    "investment": "Investition/Kapazität",
    "expansion": "Erweiterung",
    "new_product_line": "neue Produktlinie",
    "acquisition": "Übernahme",
    "divestment": "Verkauf von Unternehmensteilen",
    "merger": "Fusion",
    "restructuring": "Umstrukturierung/Geschäftsmodell",
}
EXPANSION_EVENTS = (
    "new_site",
    "new_market",
    "investment",
    "expansion",
    "new_product_line",
    "hiring",
)


@dataclass(slots=True)
class Signal:
    """Ein Wachstums- oder Rückgangssignal mit Belegstufe."""

    kind: str
    label: str
    basis: str  # confirmed | derived | unconfirmed
    direction: str  # positive | negative | neutral
    text: str
    date: str | None
    source_url: str | None = None
    learned_at: str | None = None


@dataclass(slots=True)
class GrowthView:
    """Zusammenfassung für Score und Ergebnisseite."""

    status: str = "unknown"  # strong | positive | flat | negative | unknown
    basis: str = "none"  # confirmed | derived | unconfirmed | none
    metric_text: str = "keine vergleichbare Umsatz- oder Mitarbeiterreihe"
    expansion: str = "none"
    signals: list[Signal] = field(default_factory=list)
    revenue: list[dict[str, Any]] = field(default_factory=list)
    headcount: list[dict[str, Any]] = field(default_factory=list)


def _confirmed(event: dict[str, Any]) -> str:
    if event["verification_status"] in ("confirmed", "reported") and event["confidence"] in (
        "high",
        "medium",
    ):
        return "confirmed"
    return "unconfirmed"


def _trend(series: list[dict[str, Any]]) -> tuple[Decimal | None, str]:
    for item in series:
        if item.get("currency_mode") == "converted" or item["total_index"] is None:
            continue
        span = item["spans"][item["total_index"]]
        if span["cagr_pct"] is not None:
            text = (
                f"{item['title']}: CAGR {span['cagr_pct'].replace('.', ',')} % "
                f"({span['start_label']}–{span['end_label']})"
                + ("; nicht organisch" if span["inorganic"] else "")
            )
            return Decimal(span["cagr_pct"]), text
    return None, ""


def assess_growth(
    conn: Connection, company_id: int, thresholds: dict[str, float], window_months: int
) -> GrowthView:
    """Umsatz-/Mitarbeiterentwicklung, Ereignisse, Stellenanzeigen, neue Standorte/Länder."""
    view = GrowthView()
    view.revenue = revenue_series(conn, company_id, years=10)
    view.headcount = [
        s for s in headcount_series(conn, company_id, years=10) if s["metric"] == "headcount_total"
    ]
    rate, text = _trend(view.revenue)
    if rate is None:
        rate, text = _trend(view.headcount)
    if rate is not None:
        view.metric_text = text
        view.basis = "derived"
        view.status = (
            "strong"
            if rate >= Decimal(str(thresholds["strong"]))
            else "positive"
            if rate >= Decimal(str(thresholds["positive"]))
            else "flat"
            if rate > Decimal(str(thresholds["flat"]))
            else "negative"
        )
        view.signals.append(
            Signal(
                "trend",
                "Entwicklung aus belegten Zahlen",
                "derived",
                "positive" if rate > 0 else "negative" if rate < 0 else "neutral",
                text,
                None,
            )
        )
    since = date.today() - timedelta(days=30 * window_months)
    expansion_levels: list[str] = []
    for event in repo.events_of(conn, company_id):
        if event["event_type"] not in EVENT_LABELS:
            continue
        basis = _confirmed(event)
        direction = (
            "negative"
            if event["event_type"] in ("layoff", "site_closure", "divestment")
            else "neutral"
            if event["event_type"] in ("restructuring", "merger")
            else "positive"
        )
        view.signals.append(
            Signal(
                event["event_type"],
                EVENT_LABELS[event["event_type"]],
                basis,
                direction,
                event["summary"][:300],
                event["event_date"].isoformat() if event["event_date"] else None,
                event["source_url"],
                event["captured_at"].isoformat(),
            )
        )
        recent = event["event_date"] is None or event["event_date"] >= since
        if event["event_type"] in EXPANSION_EVENTS and recent:
            expansion_levels.append(basis)
    ads = conn.execute(
        select(db.hiring_observation).where(db.hiring_observation.c.company_id == company_id)
    ).mappings()
    for ad in ads:
        view.signals.append(
            Signal(
                "job_ads",
                "Stellenanzeigen (Personalbedarf)",
                "derived",
                "positive",
                f"{ad['open_positions_count']} Stellenanzeige(n) am "
                f"{ad['observation_date']:%d.%m.%Y}"
                " – Bedarf, keine belegte Einstellung",
                ad["observation_date"].isoformat(),
            )
        )
        if ad["observation_date"] >= since:
            expansion_levels.append("derived")
    changes = conn.execute(
        select(db.profile_change).where(
            db.profile_change.c.company_id == company_id,
            db.profile_change.c.change_kind.in_(["new_location", "new_sales_country"]),
        )
    ).mappings()
    for change in changes:
        view.signals.append(
            Signal(
                change["change_kind"],
                "neu belegter Standort"
                if change["change_kind"] == "new_location"
                else "neues Land",
                "derived",
                "positive",
                change["summary"],
                change["event_date"].isoformat() if change["event_date"] else None,
                None,
                change["detected_at"].isoformat(),
            )
        )
        expansion_levels.append("derived")
    for level in ("confirmed", "derived", "unconfirmed"):
        if level in expansion_levels:
            view.expansion = level
            break
    if view.basis == "none":
        confirmed_events = [s for s in view.signals if s.basis == "confirmed"]
        if confirmed_events:
            view.basis = "confirmed"
        elif any(s.basis == "unconfirmed" for s in view.signals):
            view.basis = "unconfirmed"
    return view
