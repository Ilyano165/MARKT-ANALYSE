"""Abnahme Phase 3: Berechnungen mit bekannten Beispieldaten."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from market_intel.intel.timeseries import (
    FLOW,
    STOCK,
    Event,
    Point,
    analyze,
    cagr,
    fiscal_label,
    pct_change,
    split_series,
)

_ids = iter(range(1, 10_000))


def p(end: str, value: str, unit: str = "EUR", **kw: object) -> Point:
    return Point(
        period_end=date.fromisoformat(end),
        value=Decimal(value),
        unit=unit,
        currency=unit if unit != "persons" else None,
        observation_id=next(_ids),
        **kw,  # type: ignore[arg-type]
    )


def test_cagr_formula_matches_definition() -> None:
    # (150/100)^(1/5) - 1 = 0.08447 → 8,4 %
    assert cagr(Decimal(100), Decimal(150), 5) == Decimal("8.4")
    assert cagr(Decimal(0), Decimal(150), 5) is None
    assert cagr(Decimal(100), Decimal(150), 0) is None
    assert pct_change(Decimal(0), Decimal(5)) is None


def test_negative_growth() -> None:
    a = analyze(
        [p("2020-12-31", "100"), p("2021-12-31", "90"), p("2022-12-31", "81")], metric="revenue"
    )
    assert [r.change_pct for r in a.rows] == [None, Decimal("-10.0"), Decimal("-10.0")]
    assert [r.change_abs for r in a.rows] == [None, Decimal(-10), Decimal(-9)]
    assert a.total is not None
    assert a.total.cagr_pct == Decimal("-10.0")
    assert a.total.total_abs == Decimal(-19)
    assert a.total.total_pct == Decimal("-19.0")
    assert "^(1/2)" in a.total.formula


def test_missing_year_is_gap_not_zero_and_no_yoy_across_it() -> None:
    a = analyze(
        [p("2019-12-31", "100"), p("2020-12-31", "110"), p("2022-12-31", "121")],
        metric="revenue",
        gap_reasons={2021: "Jahresabschluss 2021 nicht veröffentlicht"},
    )
    labels = [(r.label, r.status) for r in a.rows]
    assert labels == [("2019", "first"), ("2020", "ok"), ("2021", "gap"), ("2022", "after_gap")]
    gap = a.rows[2]
    assert gap.point is None and gap.change_abs is None
    assert "nicht 0" in gap.notes[0] and "nicht veröffentlicht" in gap.notes[0]
    after = a.rows[3]
    assert after.change_pct is None  # kein Vorjahr
    assert (after.since_label, after.since_abs, after.since_pct) == ("2020", 11, Decimal("10.0"))
    assert a.total is not None
    assert a.total.years == 3 and a.total.cagr_pct == Decimal("6.6")
    assert a.total.missing_years == ["2021"]
    assert any("Zwischenjahre unbekannt" in n for n in a.total.notes)


def test_currency_change_breaks_series() -> None:
    a = analyze(
        [
            p("2020-12-31", "100", "USD"),
            p("2021-12-31", "110", "USD"),
            p("2022-12-31", "105", "EUR"),
        ],
        metric="revenue",
    )
    last = a.rows[-1]
    assert last.status == "break" and last.change_pct is None
    assert any("Währung" in n for n in last.notes)
    assert len(a.spans) == 1 and a.spans[0].end_label == "2021"
    assert a.total is None  # jüngster Wert nicht vergleichbar
    assert any("nicht vergleichbar" in n for n in a.notes)


def test_split_series_separates_units_and_known_scopes() -> None:
    pts = [
        p("2021-12-31", "100", scope="group"),
        p("2022-12-31", "110", scope="unknown"),
        p("2022-12-31", "40", scope="single_entity"),
        p("2022-12-31", "90", "USD"),
    ]
    groups = split_series(pts)
    assert sorted(len(g) for g in groups) == [1, 1, 1, 1]
    only_group = split_series(pts[:2])
    assert len(only_group) == 1  # unbekannter Scope gehört zur einzigen bekannten Reihe


def test_acquisition_marks_growth_inorganic() -> None:
    events = [
        Event(
            1, "acquisition", date(2021, 6, 1), "Übernahme der Beispiel GmbH", "high", "confirmed"
        )
    ]
    a = analyze(
        [p("2020-12-31", "100"), p("2021-12-31", "150"), p("2022-12-31", "156")],
        metric="revenue",
        events=events,
    )
    assert "inorganic" in a.rows[1].flags and a.rows[1].change_pct == Decimal("50.0")
    assert "inorganic" not in a.rows[2].flags
    assert a.total is not None and a.total.inorganic
    assert any("kein organisches Wachstum" in n for n in a.total.notes)


def test_unconfirmed_merger_hint_is_only_a_note() -> None:
    events = [Event(2, "merger", date(2021, 3, 1), "Pressetext erwähnt Fusion", "low", "reported")]
    a = analyze([p("2020-12-31", "100"), p("2021-12-31", "120")], metric="revenue", events=events)
    assert "inorganic" not in a.rows[1].flags
    assert any("möglicher Einfluss" in n for n in a.rows[1].notes)


def test_consolidation_change_and_scope_switch_break() -> None:
    events = [Event(3, "consolidation_change", date(2021, 1, 1), "Erstkonsolidierung", "high")]
    events = [Event(3, "consolidation_change", date(2021, 1, 1), "x", "high", "confirmed")]
    a = analyze([p("2020-12-31", "100"), p("2021-12-31", "120")], metric="revenue", events=events)
    assert a.rows[1].status == "break"
    b = analyze(
        [p("2020-12-31", "100", scope="single_entity"), p("2021-12-31", "300", scope="group")],
        metric="revenue",
    )
    assert b.rows[1].status == "break" and any("Konsolidierungskreis" in n for n in b.rows[1].notes)


def test_short_fiscal_year_and_year_end_shift() -> None:
    a = analyze(
        [
            p("2021-06-30", "100", period_start=date(2020, 7, 1)),
            p("2022-06-30", "110", period_start=date(2021, 7, 1)),
            p("2022-12-31", "60", period_start=date(2022, 7, 1)),  # Rumpfgeschäftsjahr
            p("2023-12-31", "125", period_start=date(2023, 1, 1)),
        ],
        metric="revenue",
    )
    assert [r.label for r in a.rows] == ["2020/21", "2021/22", "2022", "2023"]
    assert a.rows[1].change_pct == Decimal("10.0")
    assert a.rows[2].status == "break" and any("Rumpf" in n for n in a.rows[2].notes)
    assert a.rows[3].status == "break"  # Vergleich mit Rumpfjahr unzulässig
    assert fiscal_label(date(2024, 3, 31)) == "2023/24"


def test_estimates_excluded_unless_requested() -> None:
    pts = [
        p("2020-12-31", "100"),
        p("2021-12-31", "130", provenance="estimate", methodology="Umsatz je Mitarbeiter × MA"),
    ]
    a = analyze(pts, metric="revenue")
    assert len(a.rows) == 1 and a.excluded[0][1].startswith("Schätzung")
    b = analyze(pts, metric="revenue", include_estimates=True)
    assert "estimate" in b.rows[1].flags and b.total is not None and b.total.contains_estimate


def test_incomplete_headcount_stock_series() -> None:
    a = analyze(
        [
            p("2019-12-31", "1000", "persons", period_type="point_in_time"),
            p("2021-06-30", "1100", "persons", period_type="point_in_time"),
            p("2022-06-30", "1150", "persons", period_type="point_in_time"),
            p("2022-12-31", "1200", "persons", period_type="point_in_time"),
        ],
        metric="headcount_total",
        mode=STOCK,
    )
    statuses = [(r.label, r.status) for r in a.rows]
    assert statuses == [("2019", "first"), ("2020", "gap"), ("2021", "after_gap"), ("2022", "ok")]
    assert a.rows[1].point is None
    assert a.rows[2].since_abs == 100
    assert any("Stichtage" in n for n in a.rows[2].notes)
    assert a.rows[3].change_abs == 100 and len(a.rows[3].intra_year) == 1


def test_window_limits_years_and_flow_ignores_point_in_time() -> None:
    pts = [p(f"{y}-12-31", str(100 + y - 2010)) for y in range(2010, 2024)]
    a = analyze(pts, metric="revenue", years=5, mode=FLOW)
    assert [r.label for r in a.rows] == ["2019", "2020", "2021", "2022", "2023"]
    assert sum(1 for _, why in a.excluded if "Betrachtungszeitraum" in why) == 9
