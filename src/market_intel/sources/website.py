"""Unternehmenswebseiten (Schritt 3: Abruf). Ausgewertet wird in ``engine/extract.py``.

Abgerufen werden Startseite und priorisierte Unterseiten derselben Website (Impressum,
Unternehmen, Produkte, Karriere, Presse, Investor Relations) sowie verlinkte
Geschäftsberichte (PDF) auf derselben Domain oder deren Subdomains. robots.txt und
TDM-Vorbehalt prüft das Gateway; Weiterleitungen nur innerhalb der Domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from ..context import Ctx
from ..extraction.documents import report_links
from ..extraction.text import html_to_text, same_site_links

SOURCE = "website"
MAX_PDF_BYTES = 20 * 1024 * 1024
_PRIORITY = (
    (0, r"impressum|imprint|legal[-_ ]notice|kontakt"),
    (1, r"karriere|career|jobs|stellen"),
    (1, r"investor|geschaeftsbericht|geschäftsbericht|annual[-_ ]report|finanz"),
    (2, r"presse|press|news|aktuelles|neuigkeiten|blog"),
    (2, r"unternehmen|ueber-uns|über-uns|about|company|wir"),
    (3, r"produkt|product|leistung|service|loesung|lösung|branche|anwendung|referenz"),
)


@dataclass(slots=True)
class FetchedDocument:
    """Abgerufenes Dokument einer Firmenwebsite."""

    document_id: int
    url: str
    kind: str  # html | pdf


@dataclass(slots=True)
class SiteFetch:
    """Ergebnis je Domain."""

    documents: list[FetchedDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _host_allowed(host: str, base: str) -> bool:
    host = host.removeprefix("www.")
    return host == base or host.endswith("." + base)


def _fetch(
    ctx: Ctx, url: str, base: str, *, max_bytes: int = 5 * 1024 * 1024
) -> tuple[FetchedDocument | None, tuple[tuple[str, str], ...], str]:
    for _ in range(4):
        result = ctx.gateway.get(
            SOURCE,
            url,
            purpose="Unternehmenswebseite",
            respect_robots=True,
            title=url,
            max_bytes=max_bytes,
        )
        if result.location:
            target = urljoin(url, result.location)
            host = urlsplit(target).hostname or ""
            if not _host_allowed(host, base):
                return None, (), f"Weiterleitung auf fremde Domain {host} nicht verfolgt"
            url = target
            continue
        if not result.ok or result.document_id is None:
            return None, (), f"{url}: {result.status.value} {result.error}".strip()
        ctype = result.content_type.lower()
        if "pdf" in ctype or result.content[:5] == b"%PDF-":
            return FetchedDocument(result.document_id, url, "pdf"), (), ""
        if "html" in ctype or "text/plain" in ctype:
            links = html_to_text(result.text, url).links
            return FetchedDocument(result.document_id, url, "html"), links, ""
        return None, (), f"{url}: Inhaltstyp {result.content_type or 'unbekannt'} nicht ausgewertet"
    return None, (), f"{url}: zu viele Weiterleitungen"


def _rank(url: str, label: str) -> int:
    text = f"{url} {label}"
    for rank, pattern in _PRIORITY:
        if re.search(pattern, text, re.IGNORECASE):
            return rank
    return 9


def fetch_site(ctx: Ctx, domain: str) -> SiteFetch:
    """Startseite, priorisierte Unterseiten und verlinkte Berichte abrufen."""
    outcome = SiteFetch()
    base = domain.removeprefix("www.")
    max_pages = min(ctx.settings.website_max_pages, ctx.budget.max_pages_per_site)
    start, links, error = _fetch(ctx, f"https://{domain}/", base)
    if start is None and not domain.startswith("www."):
        start, links, error = _fetch(ctx, f"https://www.{domain}/", base)
    if start is None:
        outcome.errors.append(error)
        return outcome
    outcome.documents.append(start)
    all_links = list(links)
    candidates = sorted(same_site_links(links, start.url), key=lambda link: _rank(*link))
    seen = {start.url}
    for url, label in candidates:
        if sum(d.kind == "html" for d in outcome.documents) >= max_pages:
            break
        if url in seen or url.lower().endswith(".pdf") or _rank(url, label) == 9:
            continue
        seen.add(url)
        document, page_links, error = _fetch(ctx, url, base)
        if document is None:
            outcome.errors.append(error)
            continue
        outcome.documents.append(document)
        all_links += list(page_links)
    pdfs = 0
    for url, _label in report_links(tuple(all_links)):
        host = urlsplit(url).hostname or ""
        if pdfs >= ctx.budget.max_documents_per_company or url in seen:
            continue
        if urlsplit(url).scheme != "https" or not _host_allowed(host, base):
            continue
        if not url.lower().endswith(".pdf"):
            continue
        seen.add(url)
        document, _, error = _fetch(ctx, url, base, max_bytes=MAX_PDF_BYTES)
        if document is None:
            outcome.errors.append(error)
        else:
            outcome.documents.append(document)
            pdfs += 1
    return outcome
