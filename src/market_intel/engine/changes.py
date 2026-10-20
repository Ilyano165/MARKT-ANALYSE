"""Schritt 7: Veränderungen gegenüber dem Stand vor dem Auftrag historisch festhalten.

Beobachtungen werden ohnehin append-only gespeichert. Hier wird für jede in diesem Auftrag
neu gespeicherte Beobachtung festgehalten, ob sie einen neuen Zeitraum, einen ersten Wert oder
eine Abweichung vom bisherigen gültigen Wert darstellt (``change_event``). Ein Abruffehler
ändert nie den Status bestehender Beobachtungen.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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


# ---------------------------------------------------------------------- Phase 3: Profiländerungen

_FACT_KINDS = {
    "product_group": "new_product_group",
    "service": "new_service",
    "brand": "new_brand",
    "sales_channel": "new_sales_channel",
    "target_group": "new_target_group",
}
_EVENT_SUBJECT = {
    "acquisition": "Übernahme",
    "divestment": "Verkauf/Abspaltung",
    "merger": "Fusion",
    "consolidation_change": "Änderung Konsolidierungskreis",
    "fiscal_year_change": "Umstellung Geschäftsjahr",
    "investment": "angekündigte Investition",
    "new_site": "neuer Standort (Meldung)",
    "site_closure": "Standortschließung",
    "expansion": "Erweiterung",
    "restructuring": "Umstrukturierung",
    "layoff": "Stellenabbau",
    "hiring": "Einstellungen",
    "new_product_line": "neue Produktlinie",
    "new_market": "neuer Markt",
    "partnership": "Partnerschaft",
    "annual_report": "Geschäftsbericht",
    "insolvency": "Insolvenz",
}


def record_profile_changes(conn: Connection, run_id: int) -> int:
    """Neue Standorte, Märkte, Produktangaben, Ereignisse, Beziehungen und Berichte.

    Nur für Unternehmen, die schon vor dem Auftrag bekannt waren (sonst ist es die
    Ersterfassung, keine Veränderung). Jede Änderung verweist auf Beleg und Quelle;
    ``event_date`` ist das Datum laut Quelle, ``detected_at`` der Zeitpunkt, zu dem das
    System davon erfuhr. Fehlt etwas bei einer erneuten Recherche, wird **keine** Schließung
    oder Einstellung abgeleitet – Abwesenheit in einer Quelle ist kein Beleg.
    """
    run = (
        conn.execute(select(db.research_run).where(db.research_run.c.id == run_id))
        .mappings()
        .first()
    )
    if run is None:
        return 0
    since = run["created_at"]
    known = set(
        conn.execute(select(db.company.c.id).where(db.company.c.created_at < since)).scalars()
    )
    created = 0

    def add(
        company_id: int,
        subject: str,
        kind: str,
        table: str,
        ref_id: int,
        summary: str,
        event_date: Any = None,
        document_id: int | None = None,
        evidence_id: int | None = None,
    ) -> None:
        nonlocal created
        if company_id not in known:
            return
        exists = conn.execute(
            select(db.profile_change.c.id).where(
                db.profile_change.c.ref_table == table,
                db.profile_change.c.ref_id == ref_id,
                db.profile_change.c.change_kind == kind,
            )
        ).first()
        if exists:
            return
        conn.execute(
            insert(db.profile_change).values(
                company_id=company_id,
                subject=subject,
                change_kind=kind,
                ref_table=table,
                ref_id=ref_id,
                summary=summary[:500],
                event_date=event_date,
                source_document_id=document_id,
                evidence_id=evidence_id,
                research_run_id=run_id,
                detected_at=db.utcnow(),
            )
        )
        created += 1

    loc = db.company_location
    for row in conn.execute(select(loc).where(loc.c.captured_at >= since)).mappings():
        place = ", ".join(x for x in (row["city"], row["country_code"]) if x) or "Ort unbekannt"
        add(
            row["company_id"],
            "location",
            "new_location",
            "company_location",
            row["id"],
            f"Neuer Standort erfasst: {row['location_type']} – {place}",
            document_id=row["source_document_id"],
            evidence_id=row["evidence_id"],
        )

    fact = db.company_profile_fact
    for row in conn.execute(select(fact).where(fact.c.captured_at >= since)).mappings():
        kind = _FACT_KINDS.get(row["fact_type"])
        if kind is None:
            continue
        earlier = conn.execute(
            select(fact.c.id).where(
                fact.c.company_id == row["company_id"],
                fact.c.fact_type == row["fact_type"],
                fact.c.normalized == row["normalized"],
                fact.c.captured_at < since,
            )
        ).first()
        if earlier is None:
            add(
                row["company_id"],
                "product" if row["fact_type"] != "target_group" else "market",
                kind,
                "company_profile_fact",
                row["id"],
                f"Neu belegt: {row['value']}",
                document_id=row["source_document_id"],
                evidence_id=row["evidence_id"],
            )

    market = db.company_market
    for row in conn.execute(select(market).where(market.c.captured_at >= since)).mappings():
        earlier = conn.execute(
            select(market.c.id).where(
                market.c.company_id == row["company_id"],
                market.c.country_code == row["country_code"],
                market.c.region_label == row["region_label"],
                market.c.presence_type == row["presence_type"],
                market.c.captured_at < since,
            )
        ).first()
        if earlier is None and row["presence_type"] != "mentioned":
            add(
                row["company_id"],
                "market",
                "new_sales_country",
                "company_market",
                row["id"],
                f"Neues Absatzland/-gebiet ({row['presence_type']}): "
                f"{row['country_code'] or row['region_label']}",
                document_id=row["source_document_id"],
                evidence_id=row["evidence_id"],
            )

    event = db.corporate_event
    for row in conn.execute(select(event).where(event.c.captured_at >= since)).mappings():
        label = _EVENT_SUBJECT.get(row["event_type"], row["event_type"])
        add(
            row["company_id"],
            "event",
            f"event:{row['event_type']}",
            "corporate_event",
            row["id"],
            f"{label}: {row['summary'][:300]}",
            event_date=row["event_date"],
            document_id=row["source_document_id"],
            evidence_id=row["evidence_id"],
        )

    rel = db.parent_company_relationship
    for row in conn.execute(select(rel).where(rel.c.captured_at >= since)).mappings():
        add(
            row["child_company_id"],
            "structure",
            "new_parent",
            "parent_company_relationship",
            row["id"],
            f"Neue Mutterbeziehung ({row['relationship_type']})",
            document_id=row["source_document_id"],
        )
        add(
            row["parent_company_id"],
            "structure",
            "new_subsidiary",
            "parent_company_relationship",
            row["id"],
            f"Neue Tochterbeziehung ({row['relationship_type']})",
            document_id=row["source_document_id"],
        )

    fin = db.financial_observation
    docs = conn.execute(
        select(
            fin.c.company_id,
            fin.c.source_document_id,
            fin.c.period_end,
            db.source_document.c.content_type,
            db.source_document.c.url,
        )
        .join(db.source_document, db.source_document.c.id == fin.c.source_document_id)
        .where(fin.c.research_run_id == run_id, fin.c.provenance_class != "derived")
    ).all()
    for company_id, document_id, period_end, ctype, url in docs:
        is_report = "pdf" in (ctype or "") or str(url).lower().endswith(".pdf")
        earlier = conn.execute(
            select(fin.c.id).where(
                fin.c.company_id == company_id,
                fin.c.period_end == period_end,
                fin.c.captured_at < since,
            )
        ).first()
        if is_report and earlier is None:
            add(
                company_id,
                "document",
                "new_annual_report",
                "source_document",
                document_id,
                f"Neuer Bericht mit Zahlen für {period_end:%Y} ({url})",
                event_date=period_end,
                document_id=document_id,
            )
    return created


def history(conn: Connection, company_id: int) -> list[dict[str, Any]]:
    """Zeitleiste: Kennzahländerungen und Profiländerungen mit Ereignis- und Erfassungsdatum."""
    items: list[dict[str, Any]] = []
    for row in conn.execute(
        select(db.change_event).where(db.change_event.c.company_id == company_id)
    ).mappings():
        diff = None
        if row["old_value"] is not None and row["new_value"] is not None:
            diff = format(Decimal(row["new_value"]) - Decimal(row["old_value"]), "f")
        items.append(
            {
                "subject": "metric",
                "kind": row["change_kind"],
                "metric": row["metric"],
                "summary": _metric_summary(row),
                "event_date": row["period_end"].isoformat() if row["period_end"] else None,
                "detected_at": row["detected_at"].isoformat(),
                "observation_id": row["observation_id"],
                "difference": diff,
                "run_id": row["research_run_id"],
            }
        )
    for row in conn.execute(
        select(db.profile_change).where(db.profile_change.c.company_id == company_id)
    ).mappings():
        items.append(
            {
                "subject": row["subject"],
                "kind": row["change_kind"],
                "summary": row["summary"],
                "event_date": row["event_date"].isoformat() if row["event_date"] else None,
                "detected_at": row["detected_at"].isoformat(),
                "evidence_id": row["evidence_id"],
                "document_id": row["source_document_id"],
                "run_id": row["research_run_id"],
            }
        )
    items.sort(key=lambda i: (i["detected_at"], i["event_date"] or ""), reverse=True)
    return items


def _metric_summary(row: Any) -> str:
    unit = row["unit"] or ""
    period = f"{row['period_end']:%d.%m.%Y}" if row["period_end"] else "?"
    if row["change_kind"] == "first_value":
        return f"{row['metric']}: erster Wert {row['new_value']} {unit} (Stand {period})"
    if row["change_kind"] == "new_period":
        return f"{row['metric']}: neuer Zeitraum {period}: {row['new_value']} {unit}"
    return (
        f"{row['metric']} {period}: Wert geändert {row['old_value']} → {row['new_value']} {unit} "
        "(ältere Angabe bleibt erhalten)"
    )
