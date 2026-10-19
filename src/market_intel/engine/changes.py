"""Schritt 7: Veränderungen gegenüber dem Stand vor dem Auftrag historisch festhalten.

Beobachtungen werden ohnehin append-only gespeichert. Hier wird für jede in diesem Auftrag
neu gespeicherte Beobachtung festgehalten, ob sie einen neuen Zeitraum, einen ersten Wert oder
eine Abweichung vom bisherigen gültigen Wert darstellt (``change_event``). Ein Abruffehler
ändert nie den Status bestehender Beobachtungen.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Connection, insert, select

from .. import db
from ..domain import PROVENANCE_RANK, ProvenanceClass


def record_changes(conn: Connection, run_id: int) -> int:
    """Änderungsereignisse für alle neuen Beobachtungen des Auftrags."""
    created = 0
    for table in db.OBSERVATION_TABLES.values():
        new_rows = (
            conn.execute(
                select(table).where(table.c.research_run_id == run_id).order_by(table.c.id)
            )
            .mappings()
            .all()
        )
        for row in new_rows:
            if row["provenance_class"] == ProvenanceClass.DERIVED.value:
                continue
            previous = (
                conn.execute(
                    select(table).where(
                        table.c.company_id == row["company_id"],
                        table.c.metric == row["metric"],
                        table.c.id < row["id"],
                        (table.c.research_run_id != run_id) | table.c.research_run_id.is_(None),
                        table.c.verification_status.notin_(["rejected", "superseded"]),
                    )
                )
                .mappings()
                .all()
            )
            same_period = [
                p
                for p in previous
                if p["period_end"] == row["period_end"] and p["currency"] == row["currency"]
            ]
            if not previous:
                kind, old = "first_value", None
            elif not same_period:
                kind, old = "new_period", None
            else:
                best = min(
                    same_period,
                    key=lambda p: PROVENANCE_RANK[ProvenanceClass(p["provenance_class"])],
                )
                if Decimal(best["value"]) == Decimal(row["value"]):
                    continue
                kind, old = "changed_value", best["value"]
            exists = conn.execute(
                select(db.change_event.c.id).where(
                    db.change_event.c.observation_id == row["id"],
                    db.change_event.c.metric == row["metric"],
                )
            ).first()
            if exists:
                continue
            conn.execute(
                insert(db.change_event).values(
                    company_id=row["company_id"],
                    metric=row["metric"],
                    period_end=row["period_end"],
                    old_value=old,
                    new_value=row["value"],
                    unit=row["unit"],
                    change_kind=kind,
                    observation_id=row["id"],
                    research_run_id=run_id,
                    detected_at=db.utcnow(),
                )
            )
            created += 1
    return created
