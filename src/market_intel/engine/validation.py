"""Schritt 6: Plausibilitäts- und Konsistenzprüfungen (deterministisch, testbar).

``check_draft`` läuft vor jedem Speichern (Fehler → nicht gespeichert, Prüfhinweis).
``validate_companies`` prüft nach der Recherche Quer-Konsistenz (Umsatz je Mitarbeiter,
Widersprüche, Zeiträume) und schreibt Hinweise für den Bericht.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..domain import ObservationDraft, PeriodType

KNOWN_CURRENCIES = frozenset(
    {
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "JPY",
        "CNY",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "CZK",
        "HUF",
        "CAD",
        "AUD",
        "RON",
        "BGN",
        "TRY",
        "INR",
        "KRW",
        "BRL",
        "MXN",
        "ZAR",
        "SGD",
        "HKD",
        "NZD",
    }
)
MAX_REVENUE = Decimal("2e12")
MAX_HEADCOUNT = Decimal(5_000_000)
MIN_YEAR = 1950


def check_draft(draft: ObservationDraft, today: date | None = None) -> list[tuple[str, str, str]]:
    """Liste (Schwere, Code, Meldung). Schwere ``error`` verhindert das Speichern."""
    today = today or date.today()
    problems: list[tuple[str, str, str]] = []
    if draft.period_end.year < MIN_YEAR:
        problems.append(("error", "period_too_old", f"Zeitraum {draft.period_end} vor {MIN_YEAR}"))
    if draft.period_end > today + timedelta(days=400):
        problems.append(
            ("error", "period_in_future", f"Zeitraum {draft.period_end} in der Zukunft")
        )
    if draft.period_start and draft.period_type == PeriodType.FISCAL_YEAR:
        days = (draft.period_end - draft.period_start).days
        if not 300 <= days <= 400:
            problems.append(
                (
                    "warning",
                    "unusual_period_length",
                    f"Geschäftsjahr mit {days} Tagen (Rumpf- oder Langjahr?)",
                )
            )
    if draft.value < 0:
        problems.append(("error", "negative_value", f"Negativer Wert {draft.value}"))
    if draft.kind == "financial":
        if draft.currency not in KNOWN_CURRENCIES:
            problems.append(("error", "unknown_currency", f"Unbekannte Währung {draft.currency}"))
        if draft.metric == "revenue" and draft.value > MAX_REVENUE:
            problems.append(
                (
                    "error",
                    "implausible_revenue",
                    f"Umsatz {draft.value} {draft.currency} unplausibel hoch",
                )
            )
        if draft.metric == "revenue" and draft.value == 0:
            problems.append(("warning", "zero_revenue", "Umsatz 0"))
    if draft.kind == "employee":
        if draft.value != draft.value.to_integral_value():
            problems.append(
                (
                    "error",
                    "fractional_headcount",
                    f"Mitarbeiterzahl {draft.value} ist keine ganze Zahl",
                )
            )
        if not 1 <= draft.value <= MAX_HEADCOUNT:
            problems.append(
                (
                    "error",
                    "implausible_headcount",
                    f"Mitarbeiterzahl {draft.value} außerhalb 1–{MAX_HEADCOUNT}",
                )
            )
    return problems


def revenue_per_employee_issue(revenue_eur: Decimal, headcount: Decimal) -> str | None:
    """Hinweis, wenn Umsatz je Mitarbeiter außerhalb 5 000 – 5 000 000 EUR liegt."""
    if headcount <= 0:
        return None
    ratio = revenue_eur / headcount
    if ratio < 5_000 or ratio > 5_000_000:
        return (
            f"Umsatz je Mitarbeiter {ratio:,.0f} EUR unplausibel – unterschiedliche "
            "Konsolidierungskreise oder Einheitenfehler prüfen"
        )
    return None
