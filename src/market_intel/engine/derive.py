"""Abgeleitete Werte und Bewertungen: EUR-Umrechnung, Datenlücken, Produktfit.

Fit-Regeln (regelbasiert, ohne KI):
- ``strong``: ≥ 2 verschiedene Begriffe eines Anwendungsfalls auf der eigenen Webseite
- ``plausible``: 1 Begriff auf der Webseite, Begriff im rechtlichen Firmennamen, Firmenliste,
  oder Treffer der lokalen KI mit geprüftem Zitat
- ``weak``: nur Branchenzuordnung (Wikidata) oder öffentliche Ausschreibung des Auftraggebers
Ohne Beleg wird kein Fit gespeichert.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import insert, select

from .. import db, repo
from ..context import Ctx
from ..domain import (
    Confidence,
    EvidenceDraft,
    GapReason,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from ..sources import ecb

RULES_VERSION = "fit-rules-0.2.0"
CORE_METRICS = (("financial", "revenue"), ("employee", "headcount_total"))
SEARCH_PROFILE_KEY = "suchprofil"


@dataclass(frozen=True, slots=True)
class UseCaseEvidence:
    """Beleg für einen Anwendungsfall (oder das Suchprofil) in einem Dokument."""

    use_case_key: str
    term: str
    quote: str
    document_id: int
    url: str
    method: str


def convert_revenue_to_eur(ctx: Ctx, company_id: int) -> int:
    """Abgeleitete EUR-Werte mit EZB-Jahresdurchschnittskurs (Methodik gespeichert)."""
    created = 0
    with ctx.engine.connect() as conn:
        rows = [
            r
            for r in repo.observations(conn, company_id, "financial", "revenue")
            if r["currency"] not in (None, "EUR") and r["provenance_class"] != "derived"
        ]
    for currency in sorted({r["currency"] for r in rows}):
        rates, rate_doc = ecb.annual_rates(ctx, currency)
        if rate_doc is None:
            continue
        for row in (r for r in rows if r["currency"] == currency):
            year = row["period_end"].year
            rate = rates.get(year)
            if rate is None or rate == 0:
                continue
            value = (Decimal(row["value"]) / rate).quantize(Decimal(1), ROUND_HALF_UP)
            draft = ObservationDraft(
                company_id=company_id,
                kind="financial",
                metric="revenue",
                value=value,
                unit="EUR",
                currency="EUR",
                period_type=PeriodType(row["period_type"]),
                period_start=row["period_start"],
                period_end=row["period_end"],
                scope=Scope(row["consolidation_scope"]),
                provenance_class=ProvenanceClass.DERIVED,
                confidence=Confidence(row["confidence"]),
                evidence=EvidenceDraft(
                    rate_doc,
                    f"EXR A.{currency}.EUR {year}",
                    f"{year},{rate}",
                    "calculation",
                    "fx-0.1.0",
                    True,
                    calculation=f"{row['value']} {currency} / {rate}",
                ),
                methodology=f"Umrechnung mit EZB-Jahresdurchschnitt {year} "
                f"({rate} {currency} je EUR)",
                limitations="Jahresdurchschnittskurs; weicht bei abweichendem Geschäftsjahr ab",
                derived_from=(int(row["id"]),),
            )
            with ctx.engine.begin() as conn:
                if repo.ingest_observation(conn, draft, ctx.run_id).created:
                    created += 1
    return created


def record_gaps(
    ctx: Ctx,
    company_id: int,
    searched: list[str],
    metrics: tuple[tuple[str, str], ...] = CORE_METRICS,
) -> list[str]:
    """Fehlende Kennzahlen als Datenlücke dokumentieren; gibt die fehlenden zurück."""
    missing = []
    with ctx.engine.begin() as conn:
        for kind, metric in metrics:
            table = db.OBSERVATION_TABLES[kind]
            has = conn.execute(
                select(table.c.id).where(table.c.company_id == company_id, table.c.metric == metric)
            ).first()
            if has is None:
                missing.append(metric)
                repo.add_gap(
                    conn,
                    company_id,
                    metric,
                    searched or ["keine Quelle erreichbar"],
                    GapReason.NOT_FOUND if searched else GapReason.SOURCE_UNAVAILABLE,
                    f"Stand {date.today().isoformat()}",
                    ctx.run_id,
                )
    return missing


def store_fit(
    ctx: Ctx,
    company_id: int,
    use_case_key: str,
    level: str,
    rationale: str,
    evidence: Sequence[EvidenceDraft],
    assessed_by: str,
) -> None:
    """Fit mit Belegen speichern (jede Bewertung ist eine neue Zeile)."""
    if not evidence:
        raise ValueError("Produktfit ohne Beleg ist nicht zulässig")
    with ctx.engine.begin() as conn:
        evidence_ids = [repo.add_evidence(conn, e) for e in evidence]
        conn.execute(
            insert(db.company_product_fit).values(
                company_id=company_id,
                use_case_key=use_case_key,
                fit_level=level,
                rationale=rationale[:1000],
                evidence_ids=json.dumps(evidence_ids),
                assessed_by=assessed_by,
                assessed_at=db.utcnow(),
                research_run_id=ctx.run_id,
            )
        )


def record_website_fits(
    ctx: Ctx, company_id: int, findings: list[UseCaseEvidence]
) -> dict[str, str]:
    """Webseiten-Belege je Anwendungsfall zu einem Fit verdichten."""
    by_case: dict[str, list[UseCaseEvidence]] = defaultdict(list)
    for finding in findings:
        by_case[finding.use_case_key].append(finding)
    levels = {}
    for key, items in by_case.items():
        rule_terms = {i.term.lower() for i in items if i.method == "keyword_rule"}
        level = "strong" if len(rule_terms) >= 2 else "plausible"
        terms = ", ".join(sorted(rule_terms)) or "lokale KI"
        evidence = [
            EvidenceDraft(i.document_id, i.url, i.quote, i.method, RULES_VERSION, True)
            for i in items[:5]
        ]
        store_fit(
            ctx,
            company_id,
            key,
            level,
            f"Eigene Webseite/Dokumente nennen: {terms}",
            evidence,
            RULES_VERSION,
        )
        levels[key] = level
    return levels
