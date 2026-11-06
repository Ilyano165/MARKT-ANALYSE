"""Vertriebsergebnisse: Kontakt, Angebot, gewonnen, verloren – und ihre Auswertung.

Zweck: Die Bewertung soll später an echten Ergebnissen geprüft werden, nicht an Schätzungen.
Jeder Eintrag speichert die Bewertung, die beim Eintragen galt; ausgewertet wird die Bewertung
vor dem ersten Kontakt gegen das tatsächliche Ergebnis. Die Auswertung zählt nur, was erfasst
ist: Sie leitet keine Gewichte ab und zeigt Anteile erst ab einer Mindestzahl abgeschlossener
Fälle je Gruppe. Einträge sind unveränderlich; eine Korrektur ist ein neuer Eintrag.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine, insert, select

from .. import db

OUTCOMES = {
    "kontakt": "Kontakt aufgenommen",
    "angebot": "Angebot abgegeben",
    "gewonnen": "gewonnen (Auftrag erhalten)",
    "verloren": "verloren",
}
CLOSED = ("gewonnen", "verloren")
PRIORITY_BANDS = ((0, 0, "0"), (1, 34, "1–34"), (35, 59, "35–59"), (60, 100, "60–100"))
MIN_CASES_DEFAULT = 10
DIMENSIONS = {
    "segment": "Segment",
    "fit_status": "Belegstatus",
    "priority_band": "Priorität",
    "model_version": "Modellversion",
}


class OutcomeError(ValueError):
    """Ungültiger Eintrag (mit Begründung)."""


def _value(raw: str | Decimal | None, currency: str | None) -> tuple[str | None, str | None]:
    if raw in (None, ""):
        if currency:
            raise OutcomeError("Währung ohne Betrag angegeben")
        return None, None
    try:
        amount = Decimal(str(raw).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise OutcomeError(f"Betrag „{raw}“ ist keine Zahl") from exc
    if not amount.is_finite() or amount < 0:
        raise OutcomeError("Betrag muss eine Zahl ≥ 0 sein")
    code = (currency or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise OutcomeError("Zum Betrag gehört eine Währung (ISO-Code, z. B. EUR)")
    return str(amount), code


def record_outcome(
    engine: Engine,
    company_id: int,
    outcome: str,
    *,
    occurred_on: date,
    author: str,
    value: str | Decimal | None = None,
    currency: str | None = None,
    use_case_key: str | None = None,
    reason: str = "",
) -> int:
    """Ergebnis eintragen (neue Zeile). Verknüpft die zu diesem Zeitpunkt aktuelle Bewertung."""
    from .assess import latest_assessments  # Zyklus vermeiden (assess nutzt current_outcome)

    if outcome not in OUTCOMES:
        raise OutcomeError(f"Ergebnis {outcome!r} unbekannt ({', '.join(OUTCOMES)})")
    if not author.strip():
        raise OutcomeError("Autor fehlt (wer hat das Ergebnis eingetragen?)")
    if occurred_on > date.today():
        raise OutcomeError("Datum liegt in der Zukunft")
    amount, code = _value(value, currency)
    with engine.connect() as conn:
        if (
            conn.execute(select(db.company.c.id).where(db.company.c.id == company_id)).first()
            is None
        ):
            raise OutcomeError(f"Unternehmen {company_id} nicht gefunden")
    current = latest_assessments(engine, company_id)
    with engine.begin() as conn:
        return int(
            conn.execute(
                insert(db.sales_outcome).values(
                    company_id=company_id,
                    outcome=outcome,
                    occurred_on=occurred_on,
                    value=amount,
                    currency=code,
                    use_case_key=(use_case_key or "").strip()[:60] or None,
                    reason=reason.strip()[:2000],
                    author=author.strip()[:120],
                    assessment_id=current[0]["id"] if current else None,
                    created_at=db.utcnow(),
                )
            ).inserted_primary_key[0]
        )


def outcome_history(engine: Engine, company_id: int) -> list[dict[str, Any]]:
    """Alle Einträge eines Unternehmens, neueste zuerst."""
    table = db.sales_outcome
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(table).where(table.c.company_id == company_id).order_by(table.c.id.desc())
            ).mappings()
        ]


def current_outcome(engine: Engine, company_id: int) -> dict[str, Any] | None:
    """Jüngster Eintrag (gilt als aktueller Stand) oder ``None``."""
    history = outcome_history(engine, company_id)
    return history[0] if history else None


def outcome_text(entry: dict[str, Any] | None) -> str:
    """Kurztext für Listen und Export."""
    if not entry:
        return ""
    value = f", {entry['value']} {entry['currency']}" if entry["value"] else ""
    return f"{OUTCOMES[entry['outcome']]} am {entry['occurred_on']:%Y-%m-%d}{value}"


def _band(priority: int | None) -> str:
    value = int(priority or 0)
    return next(label for low, high, label in PRIORITY_BANDS if low <= value <= high)


def evaluate(engine: Engine, min_cases: int = MIN_CASES_DEFAULT) -> dict[str, Any]:
    """Bewertung vor dem ersten Kontakt ↔ tatsächliches Ergebnis. Nur gezählte Daten."""
    out, score = db.sales_outcome, db.opportunity_score
    with engine.connect() as conn:
        entries = [dict(r) for r in conn.execute(select(out).order_by(out.c.id)).mappings()]
        baseline_ids = {e["assessment_id"] for e in entries if e["assessment_id"] is not None}
        baselines = {
            r["id"]: dict(r)
            for r in conn.execute(select(score).where(score.c.id.in_(baseline_ids))).mappings()
        }
    first: dict[int, dict[str, Any]] = {}
    last: dict[int, dict[str, Any]] = {}
    for entry in entries:
        first.setdefault(entry["company_id"], entry)
        last[entry["company_id"]] = entry
    groups: dict[str, dict[str, dict[str, int]]] = {
        d: defaultdict(lambda: defaultdict(int)) for d in DIMENSIONS
    }
    won_values: dict[str, Decimal] = defaultdict(Decimal)
    closed = without_baseline = 0
    cases: list[dict[str, Any]] = []  # jeder ausgewertete Fall nachprüfbar
    for company_id, final in last.items():
        if final["outcome"] not in CLOSED:
            continue
        closed += 1
        if final["outcome"] == "gewonnen" and final["value"]:
            won_values[final["currency"]] += Decimal(final["value"])
        base = baselines.get(first[company_id]["assessment_id"])
        cases.append(
            {
                "company_id": company_id,
                "outcome": final["outcome"],
                "first_entry_id": first[company_id]["id"],
                "baseline_assessment_id": base["id"] if base else None,
            }
        )
        if base is None:
            without_baseline += 1
            continue
        details = json.loads(base["details"] or "{}")
        keys = {
            "segment": base["segment"],
            "fit_status": details.get("assessment", {}).get("fit_status", "vor Modell v3"),
            "priority_band": _band(base["priority_score"]),
            "model_version": base["model_version"],
        }
        for dimension, key in keys.items():
            cell = groups[dimension][key]
            cell["n"] += 1
            cell[final["outcome"]] += 1
    tables = {
        dimension: [
            {
                "group": key,
                "n": cell["n"],
                "gewonnen": cell["gewonnen"],
                "verloren": cell["verloren"],
                "anteil_gewonnen": round(cell["gewonnen"] / cell["n"], 3)
                if cell["n"] >= min_cases
                else None,
            }
            for key, cell in sorted(rows.items(), key=lambda kv: (-kv[1]["n"], str(kv[0])))
        ]
        for dimension, rows in groups.items()
    }
    return {
        "min_cases": min_cases,
        "companies_with_outcome": len(last),
        "open": sum(1 for e in last.values() if e["outcome"] not in CLOSED),
        "closed": closed,
        "closed_without_baseline": without_baseline,
        "won_values": {k: str(v) for k, v in sorted(won_values.items())},
        "cases": cases,
        "tables": tables,
        "enough_data": any(
            row["anteil_gewonnen"] is not None for rows in tables.values() for row in rows
        ),
    }


def evaluation_markdown(result: dict[str, Any]) -> str:
    """Auswertung als Text – ausdrücklich ohne abgeleitete Gewichte oder Prognosen."""
    lines = [
        "# Auswertung: Bewertung vor dem ersten Kontakt ↔ Vertriebsergebnis",
        "",
        f"- Unternehmen mit erfasstem Ergebnis: {result['companies_with_outcome']} "
        f"(abgeschlossen: {result['closed']}, offen: {result['open']})",
        f"- Abgeschlossen ohne Bewertung vor dem ersten Kontakt: "
        f"{result['closed_without_baseline']} (nicht ausgewertet)",
        "- Auftragswerte gewonnen: "
        + (", ".join(f"{v} {k}" for k, v in result["won_values"].items()) or "keine erfasst"),
        "",
        f"Anteile werden erst ab {result['min_cases']} abgeschlossenen Fällen je Gruppe gezeigt. "
        "Die Auswertung zählt nur erfasste Ergebnisse; sie leitet keine Gewichte ab und ist "
        "keine Prognose.",
    ]
    if not result["enough_data"]:
        lines += [
            "",
            "**Noch zu wenige abgeschlossene Fälle für Anteile.** Gezeigt werden nur Anzahlen.",
        ]
    for dimension, label in DIMENSIONS.items():
        lines += [
            "",
            f"## Nach {label}",
            "",
            "| Gruppe | Fälle | gewonnen | verloren | Anteil gewonnen |",
        ]
        lines.append("|---|---|---|---|---|")
        for row in result["tables"][dimension]:
            share = (
                f"{row['anteil_gewonnen']:.0%}"
                if row["anteil_gewonnen"] is not None
                else f"– (unter {result['min_cases']} Fällen)"
            )
            lines.append(
                f"| {row['group']} | {row['n']} | {row['gewonnen']} | {row['verloren']} | {share} |"
            )
        if not result["tables"][dimension]:
            lines.append("| – | 0 | 0 | 0 | – |")
    return "\n".join(lines) + "\n"
