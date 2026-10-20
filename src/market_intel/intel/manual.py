"""Manuelle Erfassung mit Beleg: Kennzahlen, Ereignisse, Standorte, Profilangaben.

Für Angaben aus Jahresabschlüssen (Unternehmensregister), Geschäftsberichten oder
Pressemitteilungen, die nicht automatisch abrufbar oder zuverlässig erkennbar sind.
Regeln wie bei der Automatik: Das Zitat muss wörtlich im gespeicherten Dokument stehen;
eine Zahl muss im Zitat vorkommen (außer bei Schätzungen, die eine Methodik brauchen).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, select

from .. import db, repo
from ..domain import (
    Confidence,
    EvidenceDraft,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from ..extraction.documents import PdfError, pdf_text
from ..extraction.text import html_to_text, number_in_quote, quote_in_text
from ..resolution import normalize_name

VERSION = "manual-0.1.0"


class ManualEntryError(ValueError):
    """Eingabe abgelehnt (mit Begründung für den Nutzer)."""


def document_plain_text(engine: Engine, document_id: int) -> str:
    """Lesbarer Text eines gespeicherten Dokuments."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(
                    db.source_document.c.body,
                    db.source_document.c.text,
                    db.source_document.c.content_type,
                    db.source_document.c.url,
                ).where(db.source_document.c.id == document_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ManualEntryError(f"Dokument {document_id} nicht gefunden")
    if row["text"]:
        return str(row["text"])
    body: bytes = row["body"] or b""
    if body[:5] == b"%PDF-" or "pdf" in (row["content_type"] or ""):
        try:
            text, _ = pdf_text(body)
        except PdfError as exc:
            raise ManualEntryError(str(exc)) from exc
        return text
    raw = body.decode("utf-8", errors="replace")
    if "html" in (row["content_type"] or "") or "<html" in raw[:2000].lower():
        return html_to_text(raw, row["url"]).text
    return raw


def _evidence(engine: Engine, document_id: int, quote: str, locator: str) -> EvidenceDraft:
    text = document_plain_text(engine, document_id)
    if not quote_in_text(quote, text):
        raise ManualEntryError(
            "Zitat steht nicht wörtlich im Dokument – bitte genau kopieren (Leerzeichen egal)"
        )
    return EvidenceDraft(document_id, locator or "manuell", quote, "manual", VERSION, True)


def parse_partial_date(value: str) -> tuple[date, str]:
    """„2023“, „2023-05“ oder „2023-05-17“ → (Datum, Genauigkeit)."""
    if re.fullmatch(r"\d{4}", value):
        return date(int(value), 12, 31), "year"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = (int(x) for x in value.split("-"))
        return date(year, month, 1), "month"
    return date.fromisoformat(value), "day"


def add_figure(
    engine: Engine,
    *,
    company_id: int,
    document_id: int,
    quote: str,
    locator: str,
    metric: str,
    value: Decimal,
    period_end: date,
    period_start: date | None = None,
    currency: str | None = None,
    scope: str = "unknown",
    provenance: str = "company_reported",
    segment: str = "",
    methodology: str = "",
    limitations: str = "",
    run_id: int | None = None,
) -> repo.IngestResult:
    """Kennzahl mit Beleg speichern (append-only wie jede andere Beobachtung)."""
    kinds = {"revenue": "financial", "headcount_total": "employee"}
    kinds.update({m: "employee" for m in ("headcount_avg", "headcount_fte")})
    if metric not in kinds:
        raise ManualEntryError(f"Unbekannte Kennzahl {metric!r} ({', '.join(kinds)})")
    prov = ProvenanceClass(provenance)
    if prov not in (ProvenanceClass.ESTIMATE, ProvenanceClass.DERIVED) and not number_in_quote(
        value, quote
    ):
        raise ManualEntryError(
            "Der Wert steht nicht im Zitat (bei Schätzung: --provenance estimate)"
        )
    evidence = _evidence(engine, document_id, quote, locator)
    kind = kinds[metric]
    if kind == "financial":
        if not currency or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ManualEntryError("Umsatz braucht eine Währung (ISO 4217, z. B. EUR)")
        unit = currency
        period_type = PeriodType.FISCAL_YEAR
    else:
        unit, currency = "persons", None
        period_type = (
            PeriodType.FISCAL_YEAR if metric == "headcount_avg" else PeriodType.POINT_IN_TIME
        )
    try:
        draft = ObservationDraft(
            company_id=company_id,
            kind=kind,
            metric=metric,
            value=value,
            unit=unit,
            currency=currency,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            scope=Scope(scope),
            provenance_class=prov,
            confidence=Confidence.MEDIUM if prov == ProvenanceClass.ESTIMATE else Confidence.HIGH,
            evidence=evidence,
            methodology=methodology,
            limitations=limitations or "manuell erfasst und geprüft",
            segment=segment,
        )
    except ValueError as exc:
        raise ManualEntryError(str(exc)) from exc
    with engine.begin() as conn:
        result = repo.ingest_observation(conn, draft, run_id)
    if result.rejected:
        raise ManualEntryError("abgelehnt: " + "; ".join(p[2] for p in result.problems))
    return result


def add_event(
    engine: Engine,
    *,
    company_id: int,
    document_id: int,
    quote: str,
    locator: str,
    event_type: str,
    event_date: str | None,
    summary: str,
    provenance: str = "company_reported",
    counterparty: str | None = None,
    affected_headcount: int | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    country: str | None = None,
) -> int:
    """Ereignis (z. B. Übernahme) mit Beleg; manuell geprüft = zuverlässig (bricht Reihen)."""
    if event_type not in repo.EVENT_TYPES:
        raise ManualEntryError(f"Unbekannte Ereignisart ({', '.join(repo.EVENT_TYPES)})")
    evidence = _evidence(engine, document_id, quote, locator)
    day, precision = parse_partial_date(event_date) if event_date else (None, "unknown")
    prov = ProvenanceClass(provenance)
    with engine.begin() as conn:
        counterparty_id = None
        if counterparty:
            found = (
                conn.execute(
                    select(db.company_alias.c.company_id).where(
                        db.company_alias.c.normalized == normalize_name(counterparty)
                    )
                )
                .scalars()
                .all()
            )
            counterparty_id = found[0] if len(set(found)) == 1 else None
        event_id = repo.add_event(
            conn,
            repo.EventDraft(
                company_id=company_id,
                event_type=event_type,
                summary=summary or quote[:300],
                evidence=evidence,
                provenance=prov,
                confidence="high",
                method="manual",
                event_date=day,
                date_precision=precision,
                counterparty_name=counterparty,
                counterparty_company_id=counterparty_id,
                affected_headcount=affected_headcount,
                amount=amount,
                currency=currency,
                country_code=country,
                verification_status="confirmed" if prov == ProvenanceClass.OFFICIAL else "reported",
            ),
            None,
        )
    if event_id is None:
        raise ManualEntryError("Ereignis mit diesem Beleg ist bereits erfasst")
    return event_id


def add_location(
    engine: Engine,
    *,
    company_id: int,
    document_id: int,
    quote: str,
    locator: str,
    location_type: str,
    country: str | None,
    city: str | None,
    postal_code: str | None = None,
    street: str | None = None,
    latitude: Decimal | None = None,
    longitude: Decimal | None = None,
    geo_precision: str = "none",
    geo_note: str = "",
    status: str = "active",
    provenance: str = "company_reported",
) -> int:
    """Standort mit Beleg; Koordinaten nur mit Genauigkeitsangabe und Herkunftsnotiz."""
    if latitude is not None and not geo_note:
        raise ManualEntryError("Koordinaten brauchen eine Herkunftsangabe (--geo-note)")
    evidence = _evidence(engine, document_id, quote, locator)
    with engine.begin() as conn:
        try:
            return repo.add_location(
                conn,
                company_id,
                location_type,
                country_code=country,
                city=city,
                postal_code=postal_code,
                street=street,
                document_id=document_id,
                provenance=ProvenanceClass(provenance),
                latitude=latitude,
                longitude=longitude,
                geo_precision=geo_precision,
                geo_note=geo_note,
                site_status=status,
                evidence_id=repo.add_evidence(conn, evidence),
            )
        except ValueError as exc:
            raise ManualEntryError(str(exc)) from exc


def add_fact(
    engine: Engine,
    *,
    company_id: int,
    document_id: int,
    quote: str,
    locator: str,
    fact_type: str,
    value: str,
    provenance: str = "company_reported",
) -> int | None:
    """Profilangabe (Produktgruppe, Marke, Vertriebsweg, Zielgruppe …) mit Beleg."""
    if fact_type not in repo.FACT_TYPES:
        raise ManualEntryError(f"Unbekannte Angabe ({', '.join(repo.FACT_TYPES)})")
    evidence = _evidence(engine, document_id, quote, locator)
    with engine.begin() as conn:
        return repo.add_profile_fact(
            conn,
            company_id,
            fact_type,
            value,
            evidence,
            provenance=ProvenanceClass(provenance),
            confidence="high",
            method="manual",
            run_id=None,
        )


def add_market(
    engine: Engine,
    *,
    company_id: int,
    document_id: int,
    quote: str,
    locator: str,
    country: str | None,
    region: str,
    presence: str,
    provenance: str = "company_reported",
) -> int | None:
    """Belieferter/Export-/erwähnter Markt mit Beleg."""
    evidence = _evidence(engine, document_id, quote, locator)
    with engine.begin() as conn:
        try:
            return repo.add_market(
                conn,
                company_id,
                country_code=country,
                region_label=region,
                presence_type=presence,
                basis="manuell erfasst",
                evidence=evidence,
                provenance=ProvenanceClass(provenance),
                confidence="high",
                run_id=None,
            )
        except ValueError as exc:
            raise ManualEntryError(str(exc)) from exc
