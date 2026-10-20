"""Schritt 4: Informationen aus abgerufenen Dokumenten extrahieren (deterministisch).

Je Dokument (HTML oder PDF): Umsatz mit Währung/Skala/Zeitraum, Mitarbeiter, Impressum-IDs,
Stellenanzeigen (JSON-LD), Pressemeldungen, Anwendungsfall- und Suchprofil-Belege. Die lokale
KI darf zusätzlich Anwendungsfälle mit geprüftem Zitat vorschlagen – nie Zahlen.
Fremdinhalte sind Daten: Es wird nichts ausgeführt und keiner Anweisung im Text gefolgt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import insert, select, update

from .. import db, repo
from ..context import Ctx
from ..domain import (
    Confidence,
    EvidenceDraft,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from ..extraction.documents import (
    PdfError,
    affected_headcount,
    job_postings,
    pdf_text,
    press_signals,
    register_ids,
)
from ..extraction.numbers import find_headcounts, find_money
from ..extraction.profile import (
    lexicon_hits,
    market_hints,
    organization_data,
    product_headings,
    reach_claims,
)
from ..extraction.text import find_keyword_hits, html_to_text, quote_in_text
from ..usecases import UseCase
from .derive import SEARCH_PROFILE_KEY, UseCaseEvidence, record_website_fits

VERSION = "extract-0.3.0"
_IMPRINT = re.compile(r"impressum|imprint|legal[-_ ]notice|kontakt|contact", re.IGNORECASE)
_CAREER = re.compile(r"karriere|career|jobs?|stellen", re.IGNORECASE)
_PRESS = re.compile(r"presse|press|news|aktuelles|neuigkeiten|blog", re.IGNORECASE)


@dataclass(slots=True)
class ExtractionReport:
    """Was aus den Dokumenten einer Firma gewonnen wurde."""

    documents: int = 0
    observations: int = 0
    identifiers: int = 0
    hiring: int = 0
    signals: int = 0
    fits: dict[str, str] = field(default_factory=dict)
    profile_facts: int = 0
    locations: int = 0
    markets: int = 0
    events: int = 0
    rejected: int = 0
    issues: list[str] = field(default_factory=list)


def issue(
    ctx: Ctx,
    company_id: int | None,
    severity: str,
    code: str,
    message: str,
    document_id: int | None = None,
    quote: str = "",
) -> None:
    """Prüfhinweis speichern (Schritt 6 und Extraktion)."""
    with ctx.engine.begin() as conn:
        conn.execute(
            insert(db.validation_issue).values(
                research_run_id=ctx.run_id,
                company_id=company_id,
                severity=severity,
                code=code,
                message=message[:1000],
                source_document_id=document_id,
                quote=quote[:500],
                created_at=db.utcnow(),
            )
        )


def document_text(ctx: Ctx, document_id: int) -> tuple[str, str, str, str]:
    """(Text, URL, Art, Titel) eines gespeicherten Dokuments; Text wird zwischengespeichert."""
    with ctx.engine.connect() as conn:
        row = (
            conn.execute(select(db.source_document).where(db.source_document.c.id == document_id))
            .mappings()
            .one()
        )
    body: bytes = row["body"] or b""
    ctype = (row["content_type"] or "").lower()
    if row["text"]:
        kind = "pdf" if "pdf" in ctype or body[:5] == b"%PDF-" else "html"
        return row["text"], row["url"], kind, row["title"] or ""
    if "pdf" in ctype or body[:5] == b"%PDF-":
        text, _ = pdf_text(body)
        kind, title = "pdf", row["title"] or ""
    else:
        raw = body.decode("utf-8", errors="replace")
        if "html" in ctype or "<html" in raw[:2000].lower():
            parsed = html_to_text(raw, row["url"])
            text, title = parsed.text, parsed.title or (row["title"] or "")
        else:
            text, title = raw, row["title"] or ""
        kind = "html"
    with ctx.engine.begin() as conn:
        conn.execute(
            update(db.source_document)
            .where(db.source_document.c.id == document_id)
            .values(text=text[:2_000_000])
        )
    return text, row["url"], kind, title


def extract_company_documents(
    ctx: Ctx,
    company_id: int,
    document_ids: list[int],
    cases: dict[str, UseCase],
    web_terms: list[str],
) -> ExtractionReport:
    """Alle Extraktoren auf die Dokumente einer Firma anwenden."""
    report = ExtractionReport()
    findings: list[UseCaseEvidence] = []
    for document_id in dict.fromkeys(document_ids):
        try:
            text, url, kind, title = document_text(ctx, document_id)
        except PdfError as exc:
            issue(ctx, company_id, "warning", "pdf_unreadable", str(exc), document_id)
            report.issues.append(str(exc))
            continue
        if not text.strip():
            issue(ctx, company_id, "warning", "empty_document", f"Kein Text in {url}", document_id)
            continue
        report.documents += 1
        provenance = ProvenanceClass.COMPANY_REPORTED
        _figures(ctx, company_id, document_id, url, text, kind, provenance, report)
        if kind == "html" and _IMPRINT.search(url + " " + title):
            _identifiers(ctx, company_id, document_id, text, report)
        if kind == "html" and _CAREER.search(url + " " + title):
            _jobs(ctx, company_id, document_id, url, cases, report)
        if kind == "html" and _PRESS.search(url + " " + title):
            _press(ctx, company_id, document_id, url, text, report)
        _profile(ctx, company_id, document_id, url, kind, title, text, report)
        for case in cases.values():
            for hit in find_keyword_hits(text, case.website_terms, max_hits=2):
                findings.append(
                    UseCaseEvidence(case.key, hit.term, hit.quote, document_id, url, "keyword_rule")
                )
        for hit in find_keyword_hits(text, tuple(web_terms), max_hits=3):
            findings.append(
                UseCaseEvidence(
                    SEARCH_PROFILE_KEY, hit.term, hit.quote, document_id, url, "keyword_rule"
                )
            )
        if ctx.ai.available() and cases:
            for match in ctx.ai.classify_use_cases(text, {k: c.name for k, c in cases.items()}):
                findings.append(
                    UseCaseEvidence(
                        match.label,
                        "lokale KI",
                        match.quote,
                        document_id,
                        url,
                        f"llm:{ctx.ai.name}",
                    )
                )
    report.fits = record_website_fits(ctx, company_id, findings)
    return report


def _figures(
    ctx: Ctx,
    company_id: int,
    document_id: int,
    url: str,
    text: str,
    kind: str,
    provenance: ProvenanceClass,
    report: ExtractionReport,
) -> None:
    today = date.today()
    for money in find_money(text, today)[:10]:
        if not quote_in_text(money.quote, text):
            continue
        if money.period is None:
            issue(
                ctx,
                company_id,
                "warning",
                "revenue_without_period",
                f"Umsatzangabe ohne eindeutigen Zeitraum nicht gespeichert: {money.period_issue}",
                document_id,
                money.quote,
            )
            continue
        limitations = [f"Originalangabe „{money.raw}“"]
        if not money.period.exact:
            limitations.append(
                f"Zeitraum aus Text abgeleitet ({money.period.rule}, "
                f"{money.period.fiscal_label}); genaues Geschäftsjahresende unbekannt"
            )
        if money.approximate:
            limitations.append("Näherungsangabe (rund/ca.)")
        _ingest(
            ctx,
            report,
            ObservationDraft(
                company_id=company_id,
                kind="financial",
                metric="revenue",
                value=money.value,
                unit=money.currency,
                currency=money.currency,
                period_type=PeriodType.FISCAL_YEAR,
                period_start=None,
                period_end=money.period.period_end,
                scope=Scope.UNKNOWN,
                provenance_class=provenance,
                confidence=Confidence.LOW
                if money.approximate or not money.period.exact
                else Confidence.MEDIUM,
                evidence=EvidenceDraft(
                    document_id, f"{url} ({kind})", money.quote, "regex_rule", VERSION, True
                ),
                limitations="; ".join(limitations),
            ),
        )
    for head in find_headcounts(text, today)[:5]:
        if not quote_in_text(head.quote, text):
            continue
        period_end = head.period.period_end if head.period else today
        note = (
            "Stichtag aus Text"
            if head.period and head.period.exact
            else f"Jahr aus Text ({head.period.fiscal_label})"
            if head.period
            else "Stichtag unbekannt, angesetzt ist das Abrufdatum"
        )
        _ingest(
            ctx,
            report,
            ObservationDraft(
                company_id=company_id,
                kind="employee",
                metric="headcount_total",
                value=head.value,
                unit="persons",
                period_type=PeriodType.POINT_IN_TIME,
                period_start=None,
                period_end=period_end,
                provenance_class=provenance,
                confidence=Confidence.LOW
                if head.approximate or head.period is None
                else Confidence.MEDIUM,
                evidence=EvidenceDraft(
                    document_id, f"{url} ({kind})", head.quote, "regex_rule", VERSION, True
                ),
                limitations=f"Selbstangabe; {note}"
                + ("; Näherungsangabe" if head.approximate else ""),
            ),
        )


def _ingest(ctx: Ctx, report: ExtractionReport, draft: ObservationDraft) -> None:
    with ctx.engine.begin() as conn:
        result = repo.ingest_observation(conn, draft, ctx.run_id)
    if result.rejected:
        report.rejected += 1
    elif result.created:
        report.observations += 1


def _identifiers(
    ctx: Ctx, company_id: int, document_id: int, text: str, report: ExtractionReport
) -> None:
    found = register_ids(text)
    registers = [f for f in found if f.scheme == "de_register"]
    if len(registers) > 1:
        issue(
            ctx,
            company_id,
            "warning",
            "multiple_register_ids",
            "Mehrere Registernummern im Impressum; keine übernommen",
            document_id,
            " | ".join(r.quote for r in registers),
        )
        found = [f for f in found if f.scheme != "de_register"]
    with ctx.engine.begin() as conn:
        for item in found:
            if repo.add_identifier(conn, company_id, item.scheme, item.value, document_id):
                report.identifiers += 1
            else:
                other = repo.find_company_by_identifier(conn, item.scheme, item.value)
                if other is not None and other != company_id:
                    a, b = sorted((company_id, other))
                    exists = conn.execute(
                        select(db.match_candidate.c.id).where(
                            db.match_candidate.c.company_a_id == a,
                            db.match_candidate.c.company_b_id == b,
                            db.match_candidate.c.kind == "possible_duplicate",
                        )
                    ).first()
                    if exists is None:
                        conn.execute(
                            insert(db.match_candidate).values(
                                company_a_id=a,
                                company_b_id=b,
                                score=1,
                                kind="possible_duplicate",
                                reasons=json.dumps(
                                    {
                                        "gleicher_identifikator": f"{item.scheme}={item.value}",
                                        "zitat": item.quote,
                                    },
                                    ensure_ascii=False,
                                ),
                                status="open",
                                research_run_id=ctx.run_id,
                                created_at=db.utcnow(),
                            )
                        )


def _jobs(
    ctx: Ctx,
    company_id: int,
    document_id: int,
    url: str,
    cases: dict[str, UseCase],
    report: ExtractionReport,
) -> None:
    with ctx.engine.connect() as conn:
        body = (
            conn.execute(
                select(db.source_document.c.body).where(db.source_document.c.id == document_id)
            ).scalar()
            or b""
        )
        retrieved = conn.execute(
            select(db.source_document.c.retrieved_at).where(db.source_document.c.id == document_id)
        ).scalar()
    postings = job_postings(body.decode("utf-8", errors="replace"))
    if not postings:
        return
    titles = [p.title for p in postings]
    relevant: list[str] = []
    for case in cases.values():
        if any(find_keyword_hits(t, case.website_terms) for t in titles):
            relevant.append(case.key)
    quote = "; ".join(titles[:5])
    observed: date = retrieved.date() if retrieved else date.today()
    with ctx.engine.begin() as conn:
        exists = conn.execute(
            select(db.hiring_observation.c.id).where(
                db.hiring_observation.c.company_id == company_id,
                db.hiring_observation.c.source_document_id == document_id,
            )
        ).first()
        if exists:
            return
        evidence_id = repo.add_evidence(
            conn,
            EvidenceDraft(
                document_id, f"{url} JSON-LD JobPosting", quote, "jsonld_jobposting", VERSION, True
            ),
        )
        conn.execute(
            insert(db.hiring_observation).values(
                company_id=company_id,
                observation_date=observed,
                open_positions_count=len(postings),
                job_titles=json.dumps(titles[:50], ensure_ascii=False),
                locations=json.dumps(
                    sorted({p.locality for p in postings if p.locality})[:20], ensure_ascii=False
                ),
                relevant_use_cases=json.dumps(relevant),
                source_document_id=document_id,
                evidence_id=evidence_id,
                method="schema.org JobPosting",
                research_run_id=ctx.run_id,
                captured_at=db.utcnow(),
            )
        )
    report.hiring += 1


def _press(
    ctx: Ctx, company_id: int, document_id: int, url: str, text: str, report: ExtractionReport
) -> None:
    with ctx.engine.begin() as conn:
        for signal in press_signals(text)[:10]:
            stored = repo.add_signal(
                conn,
                company_id=company_id,
                signal_type=signal.signal_type,
                event_date=signal.event_date,
                summary=signal.quote[:300],
                country_code=None,
                use_case_key=None,
                evidence=EvidenceDraft(document_id, url, signal.quote, "regex_rule", VERSION, True),
                url=url,
                provenance=ProvenanceClass.COMPANY_REPORTED,
                confidence="low",
                run_id=ctx.run_id,
            )
            if stored is not None:
                report.signals += 1
            event_type = _SIGNAL_EVENTS.get(signal.signal_type)
            if event_type is None:
                continue
            event_id = repo.add_event(
                conn,
                repo.EventDraft(
                    company_id=company_id,
                    event_type=event_type,
                    summary=signal.quote[:300],
                    evidence=EvidenceDraft(
                        document_id, url, signal.quote, "regex_rule", VERSION, True
                    ),
                    provenance=ProvenanceClass.COMPANY_REPORTED,
                    confidence="low",
                    method="regex_rule",
                    event_date=signal.event_date,
                    date_precision="day" if signal.event_date else "unknown",
                    affected_headcount=affected_headcount(signal.quote)
                    if event_type in ("layoff", "hiring")
                    else None,
                    verification_status="unverified",
                ),
                ctx.run_id,
            )
            if event_id is not None:
                report.events += 1


def summarize_report(report: ExtractionReport) -> dict[str, Any]:
    """Ergebnis für das Aufgabenprotokoll."""
    return {
        "profile_facts": report.profile_facts,
        "locations": report.locations,
        "markets": report.markets,
        "events": report.events,
        "documents": report.documents,
        "observations": report.observations,
        "identifiers": report.identifiers,
        "hiring": report.hiring,
        "signals": report.signals,
        "fits": report.fits,
        "rejected": report.rejected,
        "issues": report.issues[:5],
    }


# Pressehinweis → Ereignisart. Regel-Treffer bleiben „unverified/low“: Sie erscheinen in der
# Historie und als möglicher Einfluss, brechen aber keine Zeitreihe und machen kein Wachstum
# „anorganisch“ (dafür braucht es eine bestätigte Angabe, z. B. ``mi add-event``).
_SIGNAL_EVENTS = {
    "new_location": "new_site",
    "expansion": "expansion",
    "investment": "investment",
    "acquisition": "acquisition",
    "divestment": "divestment",
    "layoff": "layoff",
    "hiring": "hiring",
    "restructuring": "restructuring",
    "new_product_line": "new_product_line",
}


def _profile(
    ctx: Ctx,
    company_id: int,
    document_id: int,
    url: str,
    kind: str,
    title: str,
    text: str,
    report: ExtractionReport,
) -> None:
    """Profilangaben: strukturierte Daten, Länder, Reichweite, Vertriebswege, Zielgruppen."""
    provenance = ProvenanceClass.COMPANY_REPORTED
    raw = ""
    headings: tuple[str, ...] = ()
    if kind == "html":
        with ctx.engine.connect() as conn:
            body = (
                conn.execute(
                    select(db.source_document.c.body).where(db.source_document.c.id == document_id)
                ).scalar()
                or b""
            )
        raw = body.decode("utf-8", errors="replace")
        headings = html_to_text(raw, url).headings

    def ev(quote: str, method: str = "regex_rule") -> EvidenceDraft:
        return EvidenceDraft(document_id, url, quote, method, VERSION, True)

    def fact(fact_type: str, value: str, quote: str, confidence: str, method: str) -> None:
        source_text = raw if method == "jsonld" else text
        if not quote_in_text(quote, source_text):
            return
        with ctx.engine.begin() as conn:
            if repo.add_profile_fact(
                conn,
                company_id,
                fact_type,
                value,
                ev(quote, method),
                provenance=provenance,
                confidence=confidence,
                method=method,
                run_id=ctx.run_id,
            ):
                report.profile_facts += 1

    org = organization_data(raw) if raw else None
    if org is not None:
        with ctx.engine.begin() as conn:
            for name in org.names:
                repo.add_alias(conn, company_id, name, "website_name", document_id)
            for place in org.places:
                if not quote_in_text(place.quote, raw):
                    continue
                exact = place.latitude is not None
                location_id = repo.add_location(
                    conn,
                    company_id,
                    place.location_type,
                    country_code=place.country,
                    city=place.city,
                    postal_code=place.postal_code,
                    street=place.street,
                    document_id=document_id,
                    provenance=provenance,
                    latitude=place.latitude,
                    longitude=place.longitude,
                    geo_precision="site" if exact else "none",
                    geo_note="Koordinaten aus schema.org-Daten der Unternehmensseite"
                    if exact
                    else "",
                    evidence_id=repo.add_evidence(conn, ev(place.quote, "jsonld")),
                    run_id=ctx.run_id,
                )
                if location_id:
                    report.locations += 1
            for code, label in org.area_served:
                if repo.add_market(
                    conn,
                    company_id,
                    country_code=code,
                    region_label="",
                    presence_type="supplied",
                    basis=f"schema.org areaServed „{label}“",
                    evidence=ev(label, "jsonld"),
                    provenance=provenance,
                    confidence="medium",
                    run_id=ctx.run_id,
                ):
                    report.markets += 1
        for brand in org.brands:
            fact("brand", brand, brand, "medium", "jsonld")
        for offer in org.offers:
            fact("product_group", offer, offer, "medium", "jsonld")
    if kind == "html":
        for fact_type, heading in product_headings(url, title, list(headings)):
            fact(fact_type, heading, heading, "low", "heading_rule")
    for label, quote in lexicon_hits(text, "sales_channel"):
        fact("sales_channel", label, quote, "low", "keyword_rule")
    for label, quote in lexicon_hits(text, "target_group"):
        fact("target_group", label, quote, "low", "keyword_rule")
    for value, quote in reach_claims(text):
        fact("market_reach_claim", value, quote, "low", "regex_rule")
    hints = market_hints(text)
    with ctx.engine.begin() as conn:
        for hint in hints:
            if not quote_in_text(hint.quote, text):
                continue
            if hint.presence == "physical":
                repo.add_location(
                    conn,
                    company_id,
                    hint.location_type or "other_site",
                    country_code=hint.country,
                    city=None,
                    postal_code=None,
                    street=None,
                    document_id=document_id,
                    provenance=provenance,
                    geo_precision="none",
                    geo_note="nur Land bekannt (aus Text)",
                    evidence_id=repo.add_evidence(conn, ev(hint.quote)),
                    run_id=ctx.run_id,
                )
                report.locations += 1
            elif repo.add_market(
                conn,
                company_id,
                country_code=hint.country,
                region_label="",
                presence_type=hint.presence,
                basis=f"Text nennt „{hint.name}“",
                evidence=ev(hint.quote),
                provenance=provenance,
                confidence="low",
                run_id=ctx.run_id,
            ):
                report.markets += 1
