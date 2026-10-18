"""Firmenprofil aus kostenlosen Quellen: GLEIF → Wikidata → SEC → Webseite → EUR-Umrechnung.

Jede Quelle ist optional; fällt eine aus, wird das als Hinweis und Datenlücke festgehalten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from . import db, repo
from .context import Ctx
from .discovery import record_website_fits
from .domain import (
    Confidence,
    EvidenceDraft,
    GapReason,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from .resolution import name_similarity
from .sources import ecb, gleif, sec_edgar, website, wikidata
from .usecases import UseCase

GLEIF_MIN_SIMILARITY = 0.85
WIKIDATA_MIN_SIMILARITY = 0.95
CORE_METRICS = (("financial", "revenue"), ("employee", "headcount_total"))


@dataclass(slots=True)
class ProfileReport:
    """Was beim Profil gefunden wurde und was nicht."""

    query: str
    company_id: int | None = None
    sources_used: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


def profile_company(
    ctx: Ctx, query: str, cases: dict[str, UseCase], country: str | None = None
) -> ProfileReport:
    """Profil anlegen bzw. aktualisieren."""
    report = ProfileReport(query)
    lei: str | None = None
    records, doc_id = gleif.search(ctx, query, country=country)
    ranked = sorted(
        records,
        key=lambda r: -max(name_similarity(query, n) for n in (r.legal_name, *r.other_names)),
    )
    if ranked and doc_id is not None:
        best = ranked[0]
        similarity = max(name_similarity(query, n) for n in (best.legal_name, *best.other_names))
        if similarity >= GLEIF_MIN_SIMILARITY:
            report.company_id = gleif.store_record(ctx, best, doc_id)
            lei = best.lei
            report.sources_used.append("gleif")
            gleif.enrich_parents(ctx, report.company_id, best.lei)
        else:
            report.candidates.append(
                f"GLEIF: {best.legal_name} ({best.lei}), Ähnlichkeit "
                f"{similarity:.2f} – nicht übernommen"
            )

    _wikidata_step(ctx, query, lei, report)
    if report.company_id is None:
        ctx.note(f"„{query}“: keine Firma eindeutig gefunden")
        return report
    company_id = report.company_id

    with ctx.engine.connect() as conn:
        ids = repo.identifiers_of(conn, company_id)
    if "gleif" not in report.sources_used and "lei" in ids:
        record, lei_doc = gleif.fetch_by_lei(ctx, ids["lei"])
        if record is not None and lei_doc is not None:
            gleif.store_record(ctx, record, lei_doc)
            gleif.enrich_parents(ctx, company_id, record.lei)
            report.sources_used.append("gleif")
    with ctx.engine.connect() as conn:
        row = repo.company_row(conn, company_id)
    if "sec_cik" in ids:
        sec_edgar.ingest_facts(ctx, company_id, int(ids["sec_cik"]))
        report.sources_used.append("sec_edgar")
    elif row["country_code"] in (None, "US"):
        for candidate in sec_edgar.name_candidates(ctx, row["display_name"]):
            report.candidates.append(
                f"SEC: {candidate.title} (CIK {candidate.cik}, {candidate.ticker}), "
                f"Ähnlichkeit {candidate.similarity:.2f} – "
                "nur über starken Identifikator übernommen"
            )

    if row["website_domain"]:
        findings, crawl = website.analyze_company_site(
            ctx, company_id, row["website_domain"], cases
        )
        if crawl.pages:
            report.sources_used.append("website")
        for error in crawl.errors[:5]:
            ctx.note(f"Webseite {row['website_domain']}: {error}")
        record_website_fits(ctx, company_id, findings)

    convert_revenue_to_eur(ctx, company_id)
    record_gaps(ctx, company_id, report.sources_used)
    return report


def _wikidata_step(ctx: Ctx, query: str, lei: str | None, report: ProfileReport) -> None:
    hits = wikidata.search_entities(ctx, query, "de") or wikidata.search_entities(ctx, query, "en")
    for hit in hits[:5]:
        entity, _, _ = wikidata.load_entity(ctx, hit.qid)
        if entity is None:
            continue
        leis = wikidata.string_values(entity, "P1278")
        if lei and lei in leis:
            wikidata.ingest_entity(ctx, hit.qid, company_id=report.company_id)
            report.sources_used.append("wikidata")
            return
        looks_like_company = any(
            wikidata.claim_values(entity, p) for p in ("P1278", "P5531", "P452", "P1128", "P2139")
        )
        similar = name_similarity(query, wikidata.label(entity)) >= WIKIDATA_MIN_SIMILARITY
        if report.company_id is None and looks_like_company and similar:
            report.company_id = wikidata.ingest_entity(ctx, hit.qid)
            report.sources_used.append("wikidata")
            return
        if looks_like_company and similar:
            report.candidates.append(
                f"Wikidata: {wikidata.label(entity)} ({hit.qid}) – ohne gemeinsamen LEI nicht "
                "zusammengeführt (Prüfung nötig)"
            )


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


def record_gaps(ctx: Ctx, company_id: int, searched: list[str]) -> None:
    """Fehlende Kernkennzahlen als Datenlücke dokumentieren."""
    with ctx.engine.begin() as conn:
        for kind, metric in CORE_METRICS:
            table = db.OBSERVATION_TABLES[kind]
            has = conn.execute(
                select(table.c.id).where(table.c.company_id == company_id, table.c.metric == metric)
            ).first()
            if has is None:
                repo.add_gap(
                    conn,
                    company_id,
                    metric,
                    searched or ["keine Quelle erreichbar"],
                    GapReason.NOT_FOUND if searched else GapReason.SOURCE_UNAVAILABLE,
                    f"Stand {date.today().isoformat()}",
                    ctx.run_id,
                )
