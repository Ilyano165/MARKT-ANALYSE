"""Mehrjährige Entwicklung einer Kennzahl – reine Berechnung ohne Datenbank.

Regeln (PRODUCT_SPECIFICATION.md §7a):

* Fehlende Jahre bleiben Lücken (``point is None``). Sie werden nie als 0, nie als
  „unverändert“ und nie interpoliert dargestellt. Über eine Lücke hinweg gibt es keine
  Vorjahresveränderung, nur eine ausdrücklich gekennzeichnete Mehrjahresveränderung.
* Vergleichbar sind zwei Werte nur bei gleicher Einheit/Währung, gleichem bekanntem
  Konsolidierungskreis, gleichem Geschäftsjahresende und regulärer Periodenlänge
  (kein Rumpf- oder Langgeschäftsjahr). Sonst entsteht ein **Bruch**: keine Veränderung,
  neuer Abschnitt.
* Bekannte Übernahmen, Verkäufe und Fusionen im Zeitraum machen eine Veränderung
  **nicht organisch** (wird berechnet, aber gekennzeichnet). Eine Änderung des
  Konsolidierungskreises oder des Geschäftsjahres ist ein Bruch. Unbestätigte Hinweise
  (z. B. aus Pressetext-Regeln) werden nur als „möglicher Einfluss“ genannt.
* CAGR = (Endwert / Anfangswert)^(1 / Jahre) − 1, nur innerhalb eines vergleichbaren
  Abschnitts, mit mindestens einem Jahr Abstand und positiven Endpunkten.
* Schätzungen gehen nur ein, wenn ausdrücklich gewünscht, und sind dann markiert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

FLOW = "flow"  # Zeitraumgröße (Umsatz, Jahresdurchschnitt Mitarbeiter)
STOCK = "stock"  # Bestandsgröße zum Stichtag (Mitarbeiter am 31.12.)

REGULAR_DAYS = (335, 396)  # Länge eines regulären Geschäftsjahres
STRONG = ("high", "medium")


@dataclass(frozen=True, slots=True)
class Point:
    """Gültiger Wert eines Zeitraums mit allem, was zur Herkunft gehört."""

    period_end: date
    value: Decimal
    unit: str
    observation_id: int
    period_start: date | None = None
    currency: str | None = None
    scope: str = "unknown"
    period_type: str = "fiscal_year"
    provenance: str = "company_reported"
    verification: str = "reported"
    confidence: str = "medium"
    conflict: bool = False
    kind: str = "financial"
    source_key: str = ""
    source_url: str = ""
    limitations: str = ""
    methodology: str = ""

    @property
    def is_estimate(self) -> bool:
        return self.provenance == "estimate"

    @property
    def is_derived(self) -> bool:
        return self.provenance == "derived"

    @property
    def length_days(self) -> int | None:
        if self.period_start is None:
            return None
        return (self.period_end - self.period_start).days + 1


@dataclass(frozen=True, slots=True)
class Event:
    """Strukturelles Ereignis (Übernahme, Verkauf …) mit Zuverlässigkeit."""

    event_id: int
    event_type: str
    event_date: date | None
    summary: str
    confidence: str = "low"
    verification: str = "unverified"

    @property
    def reliable(self) -> bool:
        return self.verification in ("confirmed", "reported") and self.confidence in STRONG


@dataclass(slots=True)
class Row:
    """Ein Jahr der Reihe; ``point is None`` heißt: Wert unbekannt."""

    label: str
    year: int
    point: Point | None
    status: str = "ok"  # first | ok | break | gap | after_gap
    change_abs: Decimal | None = None
    change_pct: Decimal | None = None
    compared_with: str | None = None
    since_label: str | None = None  # Mehrjahresvergleich über eine Lücke
    since_abs: Decimal | None = None
    since_pct: Decimal | None = None
    flags: list[str] = field(default_factory=list)  # estimate, derived, inorganic, conflict …
    notes: list[str] = field(default_factory=list)
    intra_year: list[Point] = field(default_factory=list)  # weitere Stichtage im Jahr


@dataclass(slots=True)
class Span:
    """Entwicklung über einen vergleichbaren Abschnitt."""

    start_label: str
    end_label: str
    start_value: Decimal
    end_value: Decimal
    years: int
    total_abs: Decimal
    total_pct: Decimal | None
    cagr_pct: Decimal | None
    formula: str
    missing_years: list[str]
    inorganic: bool
    contains_estimate: bool
    notes: list[str]


@dataclass(slots=True)
class Analysis:
    """Ergebnis für eine Reihe (eine Kennzahl, eine Einheit/Währung, ein Scope)."""

    metric: str
    unit: str
    scope: str
    rows: list[Row]
    spans: list[Span]
    total: Span | None
    notes: list[str]
    excluded: list[tuple[Point, str]]

    @property
    def known_points(self) -> list[Point]:
        return [r.point for r in self.rows if r.point is not None]


# ---------------------------------------------------------------------- Hilfen


def fiscal_label(period_end: date, period_type: str = "fiscal_year") -> str:
    """„2023“ bei Kalenderjahr bzw. Stichtag, „2022/23“ bei abweichendem Geschäftsjahr."""
    if period_type == "point_in_time" or (period_end.month, period_end.day) == (12, 31):
        return str(period_end.year)
    return f"{period_end.year - 1}/{period_end.year % 100:02d}"


def _q(value: Decimal, places: str = "0.1") -> Decimal:
    return value.quantize(Decimal(places), ROUND_HALF_UP)


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    """Prozentuale Veränderung; nicht definiert bei Ausgangswert ≤ 0."""
    if old <= 0:
        return None
    return _q((new / old - 1) * 100)


def cagr(start: Decimal, end: Decimal, years: int) -> Decimal | None:
    """(Ende/Anfang)^(1/Jahre) − 1 in Prozent; None, wenn mathematisch nicht sinnvoll."""
    if years < 1 or start <= 0 or end <= 0:
        return None
    return _q(Decimal(str(((float(end) / float(start)) ** (1 / years) - 1) * 100)))


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


def _fy_end(point: Point) -> tuple[int, int]:
    return point.period_end.month, point.period_end.day


def _irregular(point: Point, mode: str) -> str | None:
    if mode != FLOW:
        return None
    days = point.length_days
    if days is None:
        return None
    if days < REGULAR_DAYS[0]:
        return f"Rumpfgeschäftsjahr ({days} Tage)"
    if days > REGULAR_DAYS[1]:
        return f"verlängertes Geschäftsjahr ({days} Tage)"
    return None


def comparability(a: Point, b: Point, mode: str) -> tuple[bool, list[str], list[str]]:
    """Ob b mit a vergleichbar ist. Gibt (vergleichbar, Bruchgründe, weiche Hinweise) zurück."""
    breaks: list[str] = []
    soft: list[str] = []
    if (a.unit, a.currency) != (b.unit, b.currency):
        breaks.append(f"Währung/Einheit gewechselt ({a.unit} → {b.unit})")
    if a.scope != b.scope:
        if "unknown" in (a.scope, b.scope):
            soft.append("Konsolidierungskreis bei einem der Werte nicht angegeben")
        else:
            breaks.append(
                f"Konsolidierungskreis gewechselt ({_scope(a.scope)} → {_scope(b.scope)})"
            )
    if mode == FLOW and _fy_end(a) != _fy_end(b):
        breaks.append(
            f"Geschäftsjahresende verschoben ({a.period_end:%d.%m.} → {b.period_end:%d.%m.})"
        )
    for point in (a, b):
        irregular = _irregular(point, mode)
        if irregular:
            breaks.append(f"{fiscal_label(point.period_end)}: {irregular}")
    if mode == STOCK:
        shift = abs((_safe_replace(a.period_end, 2000) - _safe_replace(b.period_end, 2000)).days)
        shift = min(shift, 366 - shift)
        if shift > 45:
            soft.append(
                f"unterschiedliche Stichtage ({a.period_end:%d.%m.%Y} / {b.period_end:%d.%m.%Y})"
            )
    return not breaks, breaks, soft


def _safe_replace(day: date, year: int) -> date:
    if (day.month, day.day) == (2, 29):
        return date(year, 2, 28)
    return day.replace(year=year)


def _scope(scope: str) -> str:
    return {"group": "Konzern", "single_entity": "Einzelgesellschaft"}.get(scope, "unbekannt")


def _events_between(events: list[Event], start: date, end: date) -> list[Event]:
    return [e for e in events if e.event_date is not None and start < e.event_date <= end]


# ---------------------------------------------------------------------- Kern


def _annual(points: list[Point], mode: str) -> tuple[list[Point], dict[int, list[Point]]]:
    """Bestandsgrößen: je Kalenderjahr der späteste Stichtag; weitere bleiben sichtbar."""
    if mode == FLOW:
        return sorted(points, key=lambda p: p.period_end), {}
    by_year: dict[int, list[Point]] = {}
    for point in points:
        by_year.setdefault(point.period_end.year, []).append(point)
    chosen = []
    extra: dict[int, list[Point]] = {}
    for year, items in sorted(by_year.items()):
        items.sort(key=lambda p: p.period_end)
        chosen.append(items[-1])
        extra[year] = items[:-1]
    return chosen, extra


def analyze(
    points: list[Point],
    *,
    metric: str,
    mode: str = FLOW,
    events: list[Event] | None = None,
    years: int = 10,
    include_estimates: bool = False,
    gap_reasons: dict[int, str] | None = None,
    gap_default: str = "kein Wert in den Quellen gefunden",
    as_of: date | None = None,
) -> Analysis:
    """Jahreszeilen mit Veränderungen, vergleichbare Abschnitte und Gesamtentwicklung."""
    events = events or []
    excluded: list[tuple[Point, str]] = []
    usable: list[Point] = []
    for point in points:
        if point.is_estimate and not include_estimates:
            excluded.append((point, "Schätzung – nur auf ausdrücklichen Wunsch einbezogen"))
        elif mode == FLOW and point.period_type == "point_in_time":
            excluded.append((point, "Stichtagswert ohne Zeitraum – für Zeitraumgröße ungeeignet"))
        else:
            usable.append(point)
    annual, intra = _annual(usable, mode)
    notes: list[str] = []
    if not annual:
        return Analysis(metric, "", "unknown", [], [], None, ["keine Werte"], excluded)
    last_year = annual[-1].period_end.year
    first_year = last_year - years + 1
    window = [p for p in annual if p.period_end.year >= first_year]
    for point in annual:
        if point.period_end.year < first_year:
            excluded.append((point, f"außerhalb des Betrachtungszeitraums ({years} Jahre)"))
    rows = _rows(window, mode, events, intra, gap_reasons or {}, gap_default)
    if as_of is not None and window[-1].period_end.year < as_of.year - 1:
        notes.append(
            f"Letzter bekannter Wert {fiscal_label(window[-1].period_end)}; neuere Jahre "
            "unbekannt (nicht als Stillstand zu lesen)"
        )
    undated = [e for e in events if e.event_date is None]
    for event in undated:
        notes.append(
            f"Ereignis ohne Datum ({event.event_type}: {event.summary[:80]}) – Einfluss auf "
            "die Reihe nicht zuordenbar"
        )
    spans = _spans(rows, mode, events)
    last_label = rows[-1].label if rows else None
    total = spans[-1] if spans and spans[-1].end_label == last_label else None
    if spans and total is None:
        notes.append(
            f"Der jüngste Wert ({last_label}) ist mit den Vorjahren nicht vergleichbar; "
            "keine Gesamtentwicklung bis zum aktuellen Rand"
        )
    if len(spans) > 1:
        notes.append(
            "Die Reihe enthält Brüche; Gesamtentwicklung nur für den letzten vergleichbaren "
            f"Abschnitt {spans[-1].start_label}–{spans[-1].end_label}"
        )
    unit = window[-1].unit
    scopes = {p.scope for p in window}
    return Analysis(
        metric,
        unit,
        scopes.pop() if len(scopes) == 1 else "mixed",
        rows,
        spans,
        total,
        notes,
        excluded,
    )


def _point_flags(point: Point) -> tuple[list[str], list[str]]:
    flags, notes = [], []
    if point.is_estimate:
        flags.append("estimate")
        notes.append(f"Schätzung: {point.methodology or 'Methode fehlt'}")
    if point.is_derived:
        flags.append("derived")
        notes.append(f"abgeleitet: {point.methodology}")
    if point.conflict:
        flags.append("conflict")
        notes.append("widersprüchliche Quellen für diesen Zeitraum")
    if point.verification == "unverified" or point.confidence == "low":
        flags.append("low_confidence")
    return flags, notes


def _rows(
    window: list[Point],
    mode: str,
    events: list[Event],
    intra: dict[int, list[Point]],
    gap_reasons: dict[int, str],
    gap_default: str,
) -> list[Row]:
    rows: list[Row] = []
    prev: Point | None = None
    last_known: Point | None = None
    for point in window:
        if prev is not None:
            missing = point.period_end.year - prev.period_end.year - 1
            if missing >= 1:
                for k in range(1, missing + 1):
                    year = prev.period_end.year + k
                    label = fiscal_label(_safe_replace(prev.period_end, year), prev.period_type)
                    reason = gap_reasons.get(year, gap_default)
                    rows.append(
                        Row(
                            label,
                            year,
                            None,
                            status="gap",
                            notes=[f"unbekannt – {reason} (nicht 0, nicht unverändert)"],
                        )
                    )
        label = fiscal_label(point.period_end, point.period_type)
        row = Row(
            label, point.period_end.year, point, intra_year=intra.get(point.period_end.year, [])
        )
        row.flags, row.notes = _point_flags(point)
        irregular = _irregular(point, mode)
        if irregular:
            row.notes.append(irregular)
        if prev is None:
            row.status = "first"
        else:
            after_gap = bool(rows) and rows[-1].status == "gap"
            ok, breaks, soft = comparability(prev, point, mode)
            between = _events_between(events, prev.period_end, point.period_end)
            structural = [
                e for e in between if e.event_type in ("consolidation_change", "fiscal_year_change")
            ]
            inorganic = [
                e for e in between if e.event_type in ("acquisition", "divestment", "merger")
            ]
            for event in structural:
                if event.reliable:
                    breaks.append(f"{_event_label(event)} am {event.event_date:%d.%m.%Y}")
                    ok = False
                else:
                    soft.append(f"möglicher Einfluss (unbestätigt): {_event_label(event)}")
            for event in inorganic:
                if event.reliable:
                    row.flags.append("inorganic")
                    row.notes.append(
                        f"nicht organisch: {_event_label(event)} am {event.event_date:%d.%m.%Y} "
                        f"({event.summary[:120]})"
                    )
                else:
                    soft.append(
                        f"möglicher Einfluss (unbestätigt): {_event_label(event)} – "
                        f"{event.summary[:100]}"
                    )
            row.notes.extend(soft)
            if not ok:
                row.status = "break"
                row.notes.extend(f"Bruch: {b}" for b in breaks)
            elif after_gap:
                row.status = "after_gap"
                if last_known is not None:
                    row.since_label = fiscal_label(last_known.period_end, last_known.period_type)
                    row.since_abs = point.value - last_known.value
                    row.since_pct = pct_change(last_known.value, point.value)
                    row.notes.append(
                        f"keine Vorjahresveränderung (Vorjahr unbekannt); Veränderung seit "
                        f"{row.since_label} über mehrere Jahre ausgewiesen"
                    )
            else:
                row.status = "ok"
                row.compared_with = fiscal_label(prev.period_end, prev.period_type)
                row.change_abs = point.value - prev.value
                row.change_pct = pct_change(prev.value, point.value)
                if row.change_pct is None:
                    row.notes.append("Vorjahreswert ≤ 0 – prozentuale Veränderung nicht definiert")
        rows.append(row)
        prev = point
        last_known = point
    return rows


_EVENT_LABELS = {
    "acquisition": "Übernahme",
    "divestment": "Verkauf/Abspaltung",
    "merger": "Fusion",
    "consolidation_change": "Änderung des Konsolidierungskreises",
    "fiscal_year_change": "Umstellung des Geschäftsjahres",
}


def _event_label(event: Event) -> str:
    return _EVENT_LABELS.get(event.event_type, event.event_type)


def _spans(rows: list[Row], mode: str, events: list[Event]) -> list[Span]:
    """Vergleichbare Abschnitte: neu bei jedem Bruch; Lücken unterbrechen nicht."""
    groups: list[list[Row]] = []
    current: list[Row] = []
    for row in rows:
        if row.point is None:
            current.append(row)
            continue
        if row.status == "break" and any(r.point is not None for r in current):
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)
    spans = []
    for group in groups:
        known = [r for r in group if r.point is not None]
        while group and group[-1].point is None:  # Lücken am Rand gehören nicht dazu
            group.pop()
        if len(known) < 2:
            continue
        first, last = known[0], known[-1]
        assert first.point is not None and last.point is not None
        years = round((last.point.period_end - first.point.period_end).days / 365.25)
        start, end = first.point.value, last.point.value
        rate = cagr(start, end, years)
        notes: list[str] = []
        if rate is None:
            notes.append(
                "CAGR nicht berechenbar"
                + (
                    " (Anfangs- oder Endwert ≤ 0)"
                    if start <= 0 or end <= 0
                    else " (unter einem Jahr)"
                )
            )
            formula = ""
        else:
            formula = (
                f"(({_fmt(end)} / {_fmt(start)})^(1/{years}) − 1) × 100 = "
                f"{format(rate, 'f').replace('.', ',')} %"
            )
        missing = [r.label for r in group if r.point is None]
        if missing:
            notes.append(
                "Zwischenjahre unbekannt: " + ", ".join(missing) + " – CAGR beruht nur auf den "
                "Endpunkten; der Verlauf dazwischen ist nicht belegt"
            )
        inorganic = any("inorganic" in r.flags for r in group)
        if inorganic:
            notes.append("enthält Übernahme-/Verkaufseffekte – kein organisches Wachstum")
        if any(
            e.event_date is None and e.reliable and e.event_type in _EVENT_LABELS for e in events
        ):
            notes.append("Ereignis ohne Datum könnte den Abschnitt beeinflussen")
        origins = sorted(
            {f"{r.point.source_key or '?'}/{r.point.provenance}" for r in group if r.point}
        )
        if len(origins) > 1:
            notes.append(
                "Werte aus unterschiedlichen Quellen/Herkunftsklassen ("
                + ", ".join(origins)
                + ") – Abgrenzungen können abweichen"
            )
        estimate = any("estimate" in r.flags for r in group)
        if estimate:
            notes.append("enthält Schätzwerte – Ergebnis unsicher")
        if any("derived" in r.flags for r in group):
            notes.append("enthält umgerechnete Werte – Wechselkurseffekte enthalten")
        spans.append(
            Span(
                first.label,
                last.label,
                start,
                end,
                years,
                end - start,
                pct_change(start, end),
                rate,
                formula,
                missing,
                inorganic,
                estimate,
                notes,
            )
        )
    return spans


# ---------------------------------------------------------------------- Umwandlung


def point_from_row(row: dict[str, Any], kind: str) -> Point:
    """Aus einem Ergebnis von ``repo.current_values``."""
    return Point(
        period_end=row["period_end"],
        period_start=row["period_start"],
        value=Decimal(row["value"]),
        unit=row["unit"],
        currency=row["currency"],
        scope=row["consolidation_scope"],
        period_type=row["period_type"],
        provenance=row["provenance_class"],
        verification=row["verification_status"],
        confidence=row["confidence"],
        conflict=bool(row.get("conflict")),
        observation_id=int(row["id"]),
        kind=kind,
        source_key=row.get("source_key", ""),
        source_url=row.get("source_url", ""),
        limitations=row.get("limitations", "") or "",
        methodology=row.get("methodology", "") or "",
    )


def split_series(points: list[Point]) -> list[list[Point]]:
    """Trennt nach Einheit und Konsolidierungskreis.

    Werte ohne Scope-Angabe gehören zur Reihe des einzigen bekannten Scopes; gibt es mehrere
    bekannte Scopes, bilden sie eine eigene Reihe „Konsolidierungskreis unbekannt“.
    """
    by_unit: dict[str, list[Point]] = {}
    for point in points:
        by_unit.setdefault(point.unit, []).append(point)
    result = []
    for unit_points in by_unit.values():
        known = {p.scope for p in unit_points if p.scope != "unknown"}
        if len(known) <= 1:
            result.append(unit_points)
            continue
        for scope in sorted(known):
            result.append([p for p in unit_points if p.scope == scope])
        unknown = [p for p in unit_points if p.scope == "unknown"]
        if unknown:
            result.append(unknown)
    return result


def as_dict(analysis: Analysis) -> dict[str, Any]:
    """Für JSON/Oberfläche; Decimal bleibt als Zeichenkette exakt."""

    def num(value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")

    def pt(point: Point | None) -> dict[str, Any] | None:
        if point is None:
            return None
        return {
            "observation_id": point.observation_id,
            "kind": point.kind,
            "value": num(point.value),
            "unit": point.unit,
            "period_start": point.period_start.isoformat() if point.period_start else None,
            "period_end": point.period_end.isoformat(),
            "scope": point.scope,
            "provenance": point.provenance,
            "verification": point.verification,
            "confidence": point.confidence,
            "source_key": point.source_key,
            "source_url": point.source_url,
            "limitations": point.limitations,
            "methodology": point.methodology,
        }

    return {
        "metric": analysis.metric,
        "unit": analysis.unit,
        "scope": analysis.scope,
        "notes": analysis.notes,
        "rows": [
            {
                "label": r.label,
                "year": r.year,
                "status": r.status,
                "point": pt(r.point),
                "change_abs": num(r.change_abs),
                "change_pct": num(r.change_pct),
                "compared_with": r.compared_with,
                "since_label": r.since_label,
                "since_abs": num(r.since_abs),
                "since_pct": num(r.since_pct),
                "flags": r.flags,
                "notes": r.notes,
                "intra_year": [pt(p) for p in r.intra_year],
            }
            for r in analysis.rows
        ],
        "spans": [
            {
                "start_label": s.start_label,
                "end_label": s.end_label,
                "start_value": num(s.start_value),
                "end_value": num(s.end_value),
                "years": s.years,
                "total_abs": num(s.total_abs),
                "total_pct": num(s.total_pct),
                "cagr_pct": num(s.cagr_pct),
                "formula": s.formula,
                "missing_years": s.missing_years,
                "inorganic": s.inorganic,
                "contains_estimate": s.contains_estimate,
                "notes": s.notes,
            }
            for s in analysis.spans
        ],
        "total_index": len(analysis.spans) - 1 if analysis.spans else None,
        "excluded": [{"point": pt(p), "reason": reason} for p, reason in analysis.excluded],
    }
