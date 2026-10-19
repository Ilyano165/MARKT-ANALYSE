"""Deterministische Extraktion von Geldbeträgen, Mitarbeiterzahlen und Bezugszeiträumen.

Regeln (testbar, ohne KI):
- Betrag + Skala + Währung: „1,2 Mrd. €“, „EUR 245,3 Mio.“, „TEUR 128.400“, „$3.2 billion“.
- Kennzahl nur, wenn ein Kennzahlwort (Umsatz, Umsatzerlöse, revenue, net sales, turnover)
  vor dem Betrag im selben Satz steht und kein konkurrierendes Wort (EBIT, Gewinn, Investition …)
  näher am Betrag steht.
- Zeitraum: ausdrücklich („Geschäftsjahr 2023“, „GJ 2023/24“, „zum 31.12.2023“, „FY2023“) oder
  genau eine Jahreszahl im Satz. Mehrere Jahre ohne Marker → nicht eindeutig → kein Wert.
- Jede Angabe trägt das wörtliche Zitat (den Satz), aus dem sie stammt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .text import parse_number

MIN_YEAR = 1990

_SCALES: tuple[tuple[str, Decimal], ...] = (
    (r"mrd\.?|milliarden?|billion|bn\.?", Decimal(10) ** 9),
    (r"mio\.?|millionen?|million|mn\.?|m\b", Decimal(10) ** 6),
    (r"tsd\.?|tausend|thousand|k\b", Decimal(10) ** 3),
)
_CURRENCIES: tuple[tuple[str, str], ...] = (
    (r"€|eur(?:o)?\b", "EUR"),
    (r"\$|usd\b|us-dollar|us dollar", "USD"),
    (r"chf\b|franken", "CHF"),
    (r"£|gbp\b", "GBP"),
)
_METRIC_WORDS = {
    "revenue": r"umsatz(?:erlöse|erloese|volumen)?|umsätze|revenues?|net sales|sales revenue|"
    r"turnover|erlöse aus|gesamtumsatz|konzernumsatz",
}
_COMPETING = re.compile(
    r"ebitda?|ebit\b|gewinn|ergebnis|jahresüberschuss|profit|net income|operating income|"
    r"investition|investment|capex|eigenkapital|equity|bilanzsumme|total assets|auftragseingang|"
    r"order intake|marktvolumen|market size|kosten|costs?",
    re.IGNORECASE,
)
_NUM = r"\d{1,3}(?:[.,'  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_SCALE_RE = "|".join(f"(?:{p})" for p, _ in _SCALES)
_CUR_RE = "|".join(f"(?:{p})" for p, _ in _CURRENCIES)
_MONEY = re.compile(
    rf"(?P<cur1>{_CUR_RE})?\s*(?P<tpre>T(?:EUR|€)\s*)?(?P<num>{_NUM})\s*"
    rf"(?P<scale>{_SCALE_RE})?\s*(?P<teur>T(?:EUR|€)|k€)?\s*(?P<cur2>{_CUR_RE})?",
    re.IGNORECASE,
)
_APPROX = re.compile(
    r"(rund|ca\.?|etwa|knapp|über|mehr als|about|approximately|around|over)\s*$", re.IGNORECASE
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])|\n+")


@dataclass(frozen=True, slots=True)
class Period:
    """Zugeordneter Bezugszeitraum."""

    period_end: date
    fiscal_label: str
    exact: bool  # True: Stichtag ausdrücklich genannt; False: nur Jahr bekannt
    rule: str


@dataclass(frozen=True, slots=True)
class MoneyFigure:
    """Belegter Geldbetrag."""

    metric: str
    value: Decimal  # in Währungseinheiten (nicht skaliert)
    currency: str
    raw: str
    scale_note: str
    period: Period | None
    period_issue: str
    approximate: bool
    quote: str


@dataclass(frozen=True, slots=True)
class HeadcountFigure:
    """Belegte Mitarbeiterzahl."""

    value: Decimal
    period: Period | None
    approximate: bool
    quote: str


_ABBREVIATIONS = re.compile(
    r"\b(Mio|Mrd|Tsd|ca|rd|Nr|bzw|inkl|zzgl|ggf|Dr|St|Co|Inc|Ltd|Corp|vs|z\.B|u\.a|d\.h)\.",
    re.IGNORECASE,
)
_PROTECT = "\u2024"  # Ersatzpunkt, damit Abkürzungen keine Satzgrenze bilden


def sentences(text: str) -> list[str]:
    """Text in Sätze/Zeilen zerlegen (Whitespace normalisiert, Abkürzungen geschützt)."""
    protected = _ABBREVIATIONS.sub(lambda m: m.group(1) + _PROTECT, text)
    parts = _SENTENCE_SPLIT.split(protected)
    return [re.sub(r"\s+", " ", p.replace(_PROTECT, ".")).strip() for p in parts if p and p.strip()]


def _scale(raw: str | None) -> Decimal:
    if not raw:
        return Decimal(1)
    for pattern, factor in _SCALES:
        if re.fullmatch(pattern, raw.strip(), re.IGNORECASE):
            return factor
    return Decimal(1)


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    for pattern, code in _CURRENCIES:
        if re.fullmatch(pattern, raw.strip(), re.IGNORECASE):
            return code
    return None


def assign_period(sentence: str, today: date | None = None) -> tuple[Period | None, str]:
    """Bezugszeitraum aus einem Satz. Gibt (Zeitraum, Problem) zurück."""
    today = today or date.today()
    max_year = today.year + 1
    m = re.search(r"(?:zum|per|am|as of|at)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", sentence, re.I)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return Period(date(year, month, day), str(year), True, "stichtag"), ""
        except ValueError:
            return None, f"ungültiges Datum {m.group(0)}"
    m = re.search(
        r"(?:gesch(?:ä|ae)ftsjahr(?:es)?|gj|wirtschaftsjahr|fiscal year|fiscal|fy)\s*"
        r"(\d{4})\s*/\s*(\d{2,4})",
        sentence,
        re.I,
    )
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if len(end_raw) == 4 else (start // 100) * 100 + int(end_raw)
        if end != start + 1 or not MIN_YEAR <= end <= max_year:
            return None, f"Geschäftsjahr {m.group(0)} unplausibel"
        return Period(
            date(end, 12, 31), f"{start}/{end_raw}", False, "geschaeftsjahr_abweichend"
        ), ""
    m = re.search(
        r"(?:gesch(?:ä|ae)ftsjahr(?:es)?|gj|wirtschaftsjahr|fiscal year|fy|im jahr|jahr)\s*"
        r"(\d{4})\b",
        sentence,
        re.I,
    )
    if m and MIN_YEAR <= int(m.group(1)) <= max_year:
        year = int(m.group(1))
        return Period(date(year, 12, 31), str(year), False, "jahr_ausdruecklich"), ""
    years = {
        int(y)
        for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", sentence)
        if MIN_YEAR <= int(y) <= max_year
    }
    if len(years) == 1:
        year = years.pop()
        return Period(date(year, 12, 31), str(year), False, "einzige_jahreszahl"), ""
    if len(years) > 1:
        return None, f"mehrere Jahreszahlen ohne eindeutige Zuordnung: {sorted(years)}"
    return None, "kein Bezugszeitraum im Satz"


def find_money(text: str, today: date | None = None) -> list[MoneyFigure]:
    """Umsatzangaben mit Währung, Skala und Zeitraum."""
    figures: list[MoneyFigure] = []
    for sentence in sentences(text):
        metric_hits = [
            (m.end(), metric)
            for metric, pattern in _METRIC_WORDS.items()
            for m in re.finditer(pattern, sentence, re.IGNORECASE)
        ]
        if not metric_hits:
            continue
        for match in _MONEY.finditer(sentence):
            cur = _currency(match.group("cur1")) or _currency(match.group("cur2"))
            teur = match.group("teur") or match.group("tpre")
            if teur:
                cur = cur or "EUR"
            if cur is None or not match.group("num"):
                continue
            scale = Decimal(1000) if teur else _scale(match.group("scale"))
            if scale == 1 and not teur and len(re.sub(r"\D", "", match.group("num"))) < 4:
                continue  # „Umsatz 5 €“ ohne Skala ist keine Unternehmenskennzahl
            before = [(end, metric) for end, metric in metric_hits if end <= match.start()]
            if not before:
                continue
            metric_end, metric = max(before)
            between = sentence[metric_end : match.start()]
            if _COMPETING.search(between):
                continue
            number = parse_number(match.group("num"))
            if number is None:
                continue
            period, issue = assign_period(sentence, today)
            prefix = sentence[max(0, match.start() - 25) : match.start()]
            figures.append(
                MoneyFigure(
                    metric=metric,
                    value=(number * scale).quantize(Decimal(1)),
                    currency=cur,
                    raw=match.group(0).strip(),
                    scale_note=(match.group("scale") or teur or "").strip(),
                    period=period,
                    period_issue=issue,
                    approximate=bool(_APPROX.search(prefix)),
                    quote=sentence[:1000],
                )
            )
    return figures


_HEAD_RE = re.compile(
    r"(?P<prefix>(?:über|mehr als|rund|ca\.?|etwa|knapp|more than|over|about|approximately|"
    r"around)\s+)?(?P<num>\d{1,3}(?:[.,' ]\d{3})+|\d{1,7})\s*\+?\s*"
    r"(?:Mitarbeiterinnen und Mitarbeiter|Mitarbeitende[nr]?|Mitarbeiter(?:innen)?|"
    r"Beschäftigte[n]?|Angestellte[n]?|employees|staff|people)",
    re.IGNORECASE,
)


def find_headcounts(text: str, today: date | None = None) -> list[HeadcountFigure]:
    """Mitarbeiterangaben mit (falls vorhanden) Zeitraum."""
    out = []
    for sentence in sentences(text):
        for match in _HEAD_RE.finditer(sentence):
            raw = match.group("num")
            value = parse_number(raw)
            if value is None or not 1 <= value <= 5_000_000:
                continue
            if re.fullmatch(r"(19|20)\d{2}", raw):
                continue  # Jahreszahl („2023 beschäftigte …“), keine Mitarbeiterzahl
            period, _ = assign_period(sentence, today)
            out.append(HeadcountFigure(value, period, bool(match.group("prefix")), sentence[:1000]))
    return out
