"""Customer Discovery: Anwendungsfall × Land → Kandidaten mit belegtem Produktfit.

Fit-Regeln (regelbasiert, ohne KI):
- ``strong``: ≥ 2 verschiedene Anwendungsfall-Begriffe auf der eigenen Webseite
- ``plausible``: 1 Begriff auf der Webseite, oder Begriff im rechtlichen Firmennamen,
  oder Treffer der lokalen KI mit geprüftem Zitat
- ``weak``: nur Branchenzuordnung (Wikidata) oder öffentliche Ausschreibung des Auftraggebers
Ohne Beleg wird kein Fit gespeichert.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import insert, select

from . import db, repo
from .context import Ctx
from .domain import EvidenceDraft
from .resolution import normalize_name
from .sources import gdelt, gleif, ted, wikidata
from .sources.website import UseCaseEvidence, analyze_company_site
from .usecases import UseCase

RULES_VERSION = "fit-rules-0.1.0"


@dataclass(slots=True)
class DiscoveryReport:
    """Zähler eines Discovery-Laufs."""

    use_case: str
    country: str
    companies: set[int] = field(default_factory=set)
    fits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    signals: int = 0
    websites_checked: int = 0


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
            ctx, company_id, key, level, f"Eigene Webseite nennt: {terms}", evidence, RULES_VERSION
        )
        levels[key] = level
    return levels


def discover(
    ctx: Ctx,
    case: UseCase,
    country: str,
    *,
    max_websites: int = 15,
    cases: dict[str, UseCase] | None = None,
) -> DiscoveryReport:
    """Kandidaten für einen Anwendungsfall in einem Land finden und belegen."""
    report = DiscoveryReport(case.key, country)
    with_website: list[int] = []

    for term in case.name_terms:
        records, doc_id = gleif.search(ctx, term, country=country, limit=20)
        for record in records:
            if doc_id is None or normalize_name(term) not in normalize_name(record.legal_name):
                continue
            company_id = gleif.store_record(ctx, record, doc_id)
            report.companies.add(company_id)
            store_fit(
                ctx,
                company_id,
                case.key,
                "plausible",
                f"Rechtlicher Name enthält „{term}“",
                [
                    EvidenceDraft(
                        doc_id,
                        f"data[lei={record.lei}].entity.legalName.name",
                        record.legal_name,
                        "api_field",
                        RULES_VERSION,
                        True,
                    )
                ],
                RULES_VERSION,
            )
            report.fits["plausible"] += 1

    industry_qids: list[str] = []
    for term in case.industry_terms:
        industry_qids += [h.qid for h in wikidata.search_entities(ctx, term, "de")[:3]]
    rows, sparql_doc = wikidata.companies_by_industry(ctx, sorted(set(industry_qids)), country)
    for row in rows:
        found_id = wikidata.ingest_entity(ctx, row["qid"])
        if found_id is None or sparql_doc is None:
            continue
        company_id = found_id
        report.companies.add(company_id)
        store_fit(
            ctx,
            company_id,
            case.key,
            "weak",
            "Branchenzuordnung in Wikidata (Suchbegriffe: "
            f"{', '.join(case.industry_terms)}; Einträge {', '.join(industry_qids)})",
            [
                EvidenceDraft(
                    sparql_doc,
                    "results.bindings[item]",
                    f"http://www.wikidata.org/entity/{row['qid']}",
                    "api_field",
                    RULES_VERSION,
                    True,
                )
            ],
            RULES_VERSION,
        )
        report.fits["weak"] += 1

    with ctx.engine.connect() as conn:
        for company_id in sorted(report.companies):
            domain = conn.execute(
                select(db.company.c.website_domain).where(db.company.c.id == company_id)
            ).scalar()
            if domain:
                with_website.append(company_id)
    for company_id in with_website[:max_websites]:
        with ctx.engine.connect() as conn:
            domain = repo.company_row(conn, company_id)["website_domain"]
        findings, crawl = analyze_company_site(ctx, company_id, domain, cases or {case.key: case})
        report.websites_checked += 1
        for error in crawl.errors[:2]:
            ctx.note(f"Webseite {domain}: {error}")
        for level in record_website_fits(ctx, company_id, findings).values():
            report.fits[level] += 1

    for term in case.tender_terms:
        notices, doc_id, _ = ted.search(ctx, term, country)
        if doc_id is None:
            continue
        for company_id, notice in ted.ingest_notices(ctx, notices, doc_id, case.key):
            report.companies.add(company_id)
            report.signals += 1
            store_fit(
                ctx,
                company_id,
                case.key,
                "weak",
                f"Öffentlicher Auftraggeber schreibt aus (Suchbegriff „{term}“). Kunde von "
                "Yellotools ist eher der ausführende Betrieb; Auftraggeber zeigt Marktbedarf",
                [
                    EvidenceDraft(
                        doc_id,
                        f"notices[publication-number={notice.number}].notice-title",
                        notice.title[:300],
                        "api_field",
                        RULES_VERSION,
                        True,
                    )
                ],
                RULES_VERSION,
            )
            report.fits["weak"] += 1

    for term in case.news_terms:
        articles, doc_id, _ = gdelt.search(ctx, term, country)
        if doc_id is not None:
            report.signals += gdelt.ingest_articles(ctx, articles, doc_id, case.key, country)
    return report
