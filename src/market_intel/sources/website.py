"""Unternehmenswebseiten: Selbstangaben (Mitarbeiter) und Belege für Anwendungsfälle.

robots.txt und TDM-Vorbehalt prüft das Gateway. Weiterleitungen werden nur innerhalb
derselben Domain verfolgt und jedes Ziel erneut geprüft. Keine Formulare, kein Login.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlsplit

from .. import repo
from ..context import Ctx
from ..domain import Confidence, EvidenceDraft, ObservationDraft, PeriodType, ProvenanceClass
from ..extraction.text import (
    EXTRACTOR_VERSION,
    find_employee_mentions,
    find_keyword_hits,
    html_to_text,
    quote_in_text,
    same_site_links,
)
from ..usecases import UseCase

SOURCE = "website"
_PRIORITY = re.compile(
    r"(unternehmen|ueber-uns|über-uns|about|company|leistung|service|produkt|product|"
    r"referenz|karriere|career|jobs|standort|location|kontakt|impressum|presse|news)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Page:
    """Gespeicherte Seite mit Text."""

    url: str
    document_id: int
    title: str
    text: str
    links: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class CrawlResult:
    """Ergebnis je Domain."""

    pages: list[Page] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _fetch_page(ctx: Ctx, url: str, allowed_host: str) -> tuple[Page | None, str]:
    for _ in range(4):
        result = ctx.gateway.get(
            SOURCE, url, purpose="Unternehmenswebseite", respect_robots=True, title=url
        )
        if result.location:
            target = urljoin(url, result.location)
            host = (urlsplit(target).hostname or "").removeprefix("www.")
            if host != allowed_host:
                return None, f"Weiterleitung auf fremde Domain {host} nicht verfolgt"
            url = target
            continue
        if not result.ok or result.document_id is None:
            return None, f"{url}: {result.status.value} {result.error}".strip()
        if "html" not in result.content_type and "text/plain" not in result.content_type:
            return None, f"{url}: Inhaltstyp {result.content_type} nicht ausgewertet"
        parsed = html_to_text(result.text, url)
        return Page(url, result.document_id, parsed.title, parsed.text, parsed.links), ""
    return None, f"{url}: zu viele Weiterleitungen"


def crawl(ctx: Ctx, domain: str, max_pages: int) -> CrawlResult:
    """Startseite und priorisierte Unterseiten derselben Domain laden."""
    outcome = CrawlResult()
    host = domain.removeprefix("www.")
    start, error = _fetch_page(ctx, f"https://{domain}/", host)
    if start is None and not domain.startswith("www."):
        start, error = _fetch_page(ctx, f"https://www.{domain}/", host)
    if start is None:
        outcome.errors.append(error)
        return outcome
    outcome.pages.append(start)
    links = same_site_links(start.links, start.url)
    links.sort(key=lambda link: 0 if _PRIORITY.search(link[0] + " " + link[1]) else 1)
    for url, _label in links:
        if len(outcome.pages) >= max_pages:
            break
        if any(p.url == url for p in outcome.pages):
            continue
        page, error = _fetch_page(ctx, url, host)
        if page is None:
            outcome.errors.append(error)
        else:
            outcome.pages.append(page)
    return outcome


@dataclass(frozen=True, slots=True)
class UseCaseEvidence:
    """Beleg für einen Anwendungsfall auf der Webseite."""

    use_case_key: str
    term: str
    quote: str
    document_id: int
    url: str
    method: str


def analyze_company_site(
    ctx: Ctx, company_id: int, domain: str, cases: dict[str, UseCase]
) -> tuple[list[UseCaseEvidence], CrawlResult]:
    """Webseite auswerten: Mitarbeiter-Selbstangaben speichern, Anwendungsfall-Belege liefern."""
    outcome = crawl(ctx, domain, ctx.settings.website_max_pages)
    findings: list[UseCaseEvidence] = []
    with ctx.engine.begin() as conn:
        for page in outcome.pages:
            for mention in find_employee_mentions(page.text)[:2]:
                if not quote_in_text(mention.quote, page.text):
                    continue
                repo.ingest_observation(
                    conn,
                    ObservationDraft(
                        company_id=company_id,
                        kind="employee",
                        metric="headcount_total",
                        value=mention.value,
                        unit="persons",
                        period_type=PeriodType.POINT_IN_TIME,
                        period_start=None,
                        period_end=date.today(),
                        provenance_class=ProvenanceClass.COMPANY_REPORTED,
                        confidence=Confidence.LOW if mention.approximate else Confidence.MEDIUM,
                        evidence=EvidenceDraft(
                            page.document_id,
                            page.url,
                            mention.quote,
                            "regex_rule",
                            EXTRACTOR_VERSION,
                            True,
                        ),
                        limitations="Selbstangabe auf Webseite; Stichtag unbekannt, "
                        "angesetzt ist das Abrufdatum"
                        + ("; Näherungsangabe" if mention.approximate else ""),
                    ),
                    ctx.run_id,
                )
    for page in outcome.pages:
        for case in cases.values():
            for hit in find_keyword_hits(page.text, case.website_terms, max_hits=2):
                findings.append(
                    UseCaseEvidence(
                        case.key, hit.term, hit.quote, page.document_id, page.url, "keyword_rule"
                    )
                )
        if ctx.llm.available():
            for match in ctx.llm.classify_use_cases(
                page.text, {k: c.name for k, c in cases.items()}
            ):
                findings.append(
                    UseCaseEvidence(
                        match.use_case_key,
                        "lokale KI",
                        match.quote,
                        page.document_id,
                        page.url,
                        f"llm:{ctx.llm.name}",
                    )
                )
    return findings, outcome
