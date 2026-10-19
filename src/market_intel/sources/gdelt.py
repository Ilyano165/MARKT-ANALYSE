"""GDELT DOC 2.0: Nachrichtensuche nach Anwendungsfall (Titel, URL, Datum – kein Artikeltext)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from .. import db, repo
from ..context import Ctx
from ..domain import EvidenceDraft, ProvenanceClass
from ..resolution import normalize_name
from .common import json_object

SOURCE = "gdelt"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
VERSION = "gdelt-0.1.0"
LANG = {
    "DE": "german",
    "AT": "german",
    "CH": "german",
    "FR": "french",
    "IT": "italian",
    "ES": "spanish",
    "NL": "dutch",
    "PL": "polish",
    "GB": "english",
    "US": "english",
}


@dataclass(frozen=True, slots=True)
class Article:
    """Nachrichtenmeldung."""

    url: str
    title: str
    seen: date | None
    domain: str


def search(
    ctx: Ctx, term: str, country_iso2: str | None, timespan: str = "12m"
) -> tuple[list[Article], int | None, str]:
    """Artikel zu einem Begriff in der Landessprache."""
    language = LANG.get(country_iso2 or "")
    query = f'"{term}"' + (f" sourcelang:{language}" if language else "")
    result = ctx.gateway.get(
        SOURCE,
        URL,
        params={
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": "50",
            "timespan": timespan,
            "sort": "datedesc",
        },
        purpose=f"GDELT „{term}“",
        title=f"GDELT {term}",
    )
    if not result.ok:
        ctx.note(f"GDELT „{term}“ fehlgeschlagen: {result.status.value} {result.error}")
        return [], None, ""
    try:
        data = json_object(ctx, result)
    except ValueError:
        ctx.note(f"GDELT „{term}“: keine JSON-Antwort ({result.text[:120]!r})")
        return [], result.document_id, ""
    articles = []
    for item in data.get("articles", []):
        seen = None
        raw = str(item.get("seendate", ""))
        if len(raw) >= 8 and raw[:8].isdigit():
            seen = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        if item.get("url") and item.get("title"):
            articles.append(
                Article(str(item["url"]), str(item["title"]), seen, str(item.get("domain", "")))
            )
    return articles, result.document_id, result.text


def ingest_articles(
    ctx: Ctx,
    articles: list[Article],
    document_id: int,
    use_case_key: str | None,
    country_iso2: str | None,
) -> int:
    """Meldungen als Marktsignale; Zuordnung zu bekannten Firmen nur bei Namensnennung."""
    with ctx.engine.connect() as conn:
        known = [
            (r.company_id, r.normalized)
            for r in conn.execute(
                select(db.company_alias.c.company_id, db.company_alias.c.normalized)
            )
            if len(r.normalized) >= 5
        ]
    stored = 0
    for article in articles:
        title_norm = f" {normalize_name(article.title)} "
        matched = sorted({cid for cid, name in known if f" {name} " in title_norm})
        quote = article.title[:300]  # Feldwert aus dem gespeicherten JSON-Dokument
        with ctx.engine.begin() as conn:
            for company_id in matched or [None]:
                if (
                    repo.add_signal(
                        conn,
                        company_id=company_id,
                        signal_type="news_mention",
                        event_date=article.seen,
                        summary=f"Meldung: {article.title}",
                        country_code=country_iso2,
                        use_case_key=use_case_key,
                        evidence=EvidenceDraft(
                            document_id,
                            f"articles[url={article.url}].title",
                            quote,
                            "api_field",
                            VERSION,
                            True,
                        ),
                        url=article.url,
                        provenance=ProvenanceClass.THIRD_PARTY,
                        confidence="low",
                        run_id=ctx.run_id,
                    )
                    is not None
                ):
                    stored += 1
    return stored
