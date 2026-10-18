"""TED Search API v3: EU-Ausschreibungen als Bedarfssignale.

Öffentliche Auftraggeber, die passende Leistungen ausschreiben (z. B. Fahrzeugfolierung),
sind potenzielle Kunden. Gespeichert werden nur Metadaten und der Titel als Beleg.
Feldnamen laut Dokumentation/Wiederverwender-Beispielen; ein fehlender Gewinner ist keine
Fehlerursache.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .. import repo
from ..context import Ctx
from ..domain import EvidenceDraft, ProvenanceClass
from ..resolution import normalize_name

SOURCE = "ted"
URL = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "publication-date",
    "classification-cpv",
    "notice-type",
]
VERSION = "ted-0.1.0"
ISO3_TO_ISO2 = {
    "DEU": "DE",
    "AUT": "AT",
    "CHE": "CH",
    "FRA": "FR",
    "ITA": "IT",
    "ESP": "ES",
    "NLD": "NL",
    "BEL": "BE",
    "POL": "PL",
    "CZE": "CZ",
    "DNK": "DK",
    "SWE": "SE",
    "FIN": "FI",
    "IRL": "IE",
    "PRT": "PT",
    "LUX": "LU",
    "HUN": "HU",
    "SVK": "SK",
    "SVN": "SI",
    "ROU": "RO",
    "BGR": "BG",
    "HRV": "HR",
    "GRC": "GR",
    "EST": "EE",
    "LVA": "LV",
    "LTU": "LT",
    "NOR": "NO",
    "GBR": "GB",
}
ISO2_TO_ISO3 = {v: k for k, v in ISO3_TO_ISO2.items()}


@dataclass(frozen=True, slots=True)
class Notice:
    """Ausgewertete Bekanntmachung."""

    number: str
    title: str
    buyers: tuple[str, ...]
    country_iso3: str | None
    published: date | None


def _first_text(value: Any, prefer: tuple[str, ...] = ("deu", "eng")) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _first_text(value[0], prefer) if value else ""
    if isinstance(value, dict):
        for lang in prefer:
            if lang in value:
                return _first_text(value[lang], prefer)
        return _first_text(next(iter(value.values()), ""), prefer)
    return ""


def _all_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _all_texts(v)]
    if isinstance(value, dict):
        for lang in ("deu", "eng"):
            if lang in value:
                return _all_texts(value[lang])
        return [t for v in value.values() for t in _all_texts(v)][:1]
    return []


def parse_notice(item: dict[str, Any]) -> Notice | None:
    """Eine Bekanntmachung auswerten."""
    number = str(item.get("publication-number", ""))
    title = _first_text(item.get("notice-title"))
    if not number or not title:
        return None
    published = None
    raw_date = str(item.get("publication-date", ""))[:10]
    with contextlib.suppress(ValueError):
        published = date.fromisoformat(raw_date)
    country = _first_text(item.get("buyer-country")) or None
    buyers = tuple(
        dict.fromkeys(b.strip() for b in _all_texts(item.get("buyer-name")) if b.strip())
    )
    return Notice(number, title.strip(), buyers, country, published)


def search(
    ctx: Ctx, term: str, country_iso2: str, months: int = 24, limit: int = 50
) -> tuple[list[Notice], int | None, str]:
    """Volltextsuche in Bekanntmachungen eines Landes. Gibt (Treffer, Dokument, Rohtext)."""
    iso3 = ISO2_TO_ISO3.get(country_iso2)
    if iso3 is None:
        return [], None, ""
    since = (date.today() - timedelta(days=30 * months)).strftime("%Y%m%d")
    safe_term = term.replace('"', "")
    payload = {
        "query": f'FT~"{safe_term}" AND buyer-country={iso3} AND PD>={since} '
        "SORT BY publication-date DESC",
        "fields": FIELDS,
        "limit": limit,
        "page": 1,
        "scope": "ALL",
    }
    result = ctx.gateway.post_json(
        SOURCE, URL, payload, purpose=f"TED „{term}“ {iso3}", title=f"TED-Suche {term} {iso3}"
    )
    if not result.ok:
        ctx.note(f"TED-Suche „{term}“ fehlgeschlagen: {result.status.value} {result.error}")
        return [], None, ""
    notices = [n for n in (parse_notice(i) for i in result.json().get("notices", [])) if n]
    return notices, result.document_id, result.text


def ingest_notices(
    ctx: Ctx, notices: list[Notice], document_id: int, use_case_key: str
) -> list[tuple[int, Notice]]:
    """Auftraggeber als Firmen (öffentliche Auftraggeber) und Signale speichern."""
    company_ids: list[tuple[int, Notice]] = []
    for notice in notices:
        quote = notice.title[:300]  # Feldwert aus dem gespeicherten JSON-Dokument
        country = ISO3_TO_ISO2.get(notice.country_iso3 or "")
        url = f"https://ted.europa.eu/en/notice/-/detail/{notice.number}"
        with ctx.engine.begin() as conn:
            for buyer in notice.buyers[:3]:
                key = f"{notice.country_iso3}:{normalize_name(buyer)}"
                company_id = repo.find_company_by_identifier(conn, "ted_buyer", key)
                if company_id is None:
                    company_id = repo.create_company(
                        conn, buyer, country_code=country, entity_kind="public_buyer"
                    )
                    repo.add_identifier(conn, company_id, "ted_buyer", key, document_id)
                    repo.add_alias(conn, company_id, buyer, "ted_buyer_name", document_id)
                repo.add_signal(
                    conn,
                    company_id=company_id,
                    signal_type="tender_published",
                    event_date=notice.published,
                    summary=f"Ausschreibung: {notice.title}",
                    country_code=country,
                    use_case_key=use_case_key,
                    evidence=EvidenceDraft(
                        document_id,
                        f"notices[publication-number={notice.number}].notice-title",
                        quote,
                        "api_field",
                        VERSION,
                        True,
                    ),
                    url=url,
                    provenance=ProvenanceClass.OFFICIAL,
                    confidence="medium",
                    run_id=ctx.run_id,
                )
                company_ids.append((company_id, notice))
    return company_ids
