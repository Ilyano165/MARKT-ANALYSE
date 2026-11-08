"""TED Search API v3: EU-Ausschreibungen.

Zwei Rollen werden streng getrennt (Audit P1-2: 4 von 5 ausgewählten „Kandidaten“ waren
Behörden, die Folierung *einkaufen*):

* **Auftraggeber** (``buyer-name``) kaufen die Leistung ein – sie sind keine Verarbeiter und
  werden nur als Firma der Art ``public_buyer`` mit einem Bedarfssignal gespeichert, nie als
  Kandidat ausgewählt.
* **Zuschlagsempfänger** (``winner-name``, nur in Zuschlagsbekanntmachungen ``can-*``) führen
  die Leistung aus – sie sind mögliche Verarbeiter. Weil Bekanntmachungen mehrere Lose haben
  (Live-Abruf 2026-11-08: „Lieferung Führungskraftwagen“ mit Losen für Fahrzeug, Ausbau und
  Beschriftung; Gewinner u. a. MAN Truck & Bus), wird ein Gewinner nur übernommen, wenn der
  **Titel** der Bekanntmachung den Suchbegriff enthält. Die USt-IdNr. (``winner-identifier``)
  dient als Kennung.

Gespeichert werden nur Metadaten und der Titel als Beleg.
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
from .common import json_object

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
    "winner-name",
    "winner-country",
    "winner-identifier",
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
    notice_type: str = ""
    winners: tuple[tuple[str, str | None, str | None], ...] = ()  # (Name, Land ISO3, USt-IdNr.)

    @property
    def is_award(self) -> bool:
        """Zuschlagsbekanntmachung (eForms ``can-*``) – nur dort stehen Auftragnehmer."""
        return self.notice_type.startswith("can")

    def title_mentions(self, term: str) -> bool:
        return term.casefold() in self.title.casefold()


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
    notice_type = _first_text(item.get("notice-type"))
    names = [w.strip() for w in _all_texts(item.get("winner-name"))]
    raw_countries = item.get("winner-country")
    raw_ids = item.get("winner-identifier")
    countries: list[Any] = raw_countries if isinstance(raw_countries, list) else []
    ids: list[Any] = raw_ids if isinstance(raw_ids, list) else []
    winners: dict[str, tuple[str, str | None, str | None]] = {}
    # Die Listen sind NICHT positionsgleich (Live-Abruf 532438-2024: Reihenfolge von
    # winner-name und winner-identifier weicht ab). Kennung und Land daher nur bei genau einem
    # Gewinner zuordnen – sonst bliebe einer Firma womöglich die USt-IdNr. einer anderen.
    single = len(names) == 1 and len(ids) == 1 and len(countries) == 1
    for name in names:
        if not name or name in winners:
            continue
        winners[name] = (
            name,
            str(countries[0]) if single else None,
            vat_id(str(ids[0])) if single else None,
        )
    return Notice(
        number, title.strip(), buyers, country, published, notice_type, tuple(winners.values())
    )


def vat_id(raw: str) -> str | None:
    """USt-IdNr. normalisieren („DE 811 125 281“ → „DE811125281“); sonst None."""
    value = "".join(raw.split()).upper()
    return value if 4 <= len(value) <= 16 and value[:2].isalpha() else None


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
    items = json_object(ctx, result).get("notices")
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    notices = [n for n in (parse_notice(i) for i in items) if n]
    return notices, result.document_id, result.text


def ingest_winners(
    ctx: Ctx, notices: list[Notice], document_id: int, use_case_key: str, term: str
) -> list[tuple[int, Notice]]:
    """Zuschlagsempfänger als mögliche Verarbeiter speichern (nur Zuschläge, deren Titel den
    Suchbegriff enthält – Lose anderer Leistungen werden nicht zugeordnet)."""
    out: list[tuple[int, Notice]] = []
    for notice in notices:
        if not (notice.is_award and notice.title_mentions(term)):
            continue
        url = f"https://ted.europa.eu/en/notice/-/detail/{notice.number}"
        with ctx.engine.begin() as conn:
            for name, country3, vat in notice.winners[:5]:
                # Land nur, wenn gemeldet – nicht aus dem Land des Auftraggebers ableiten
                country = ISO3_TO_ISO2.get(country3 or "")
                key = f"{country3 or '?'}:{normalize_name(name)}"
                company_id = (
                    repo.find_company_by_identifier(conn, "vat", vat) if vat else None
                ) or repo.find_company_by_identifier(conn, "ted_winner", key)
                if company_id is None:
                    company_id = repo.create_company(conn, name, country_code=country)
                    repo.add_identifier(conn, company_id, "ted_winner", key, document_id)
                    if vat:
                        repo.add_identifier(conn, company_id, "vat", vat, document_id)
                    repo.add_alias(conn, company_id, name, "ted_winner_name", document_id)
                repo.add_signal(
                    conn,
                    company_id=company_id,
                    signal_type="tender_awarded",
                    event_date=notice.published,
                    summary=f"Zuschlag erhalten: {notice.title}",
                    country_code=country,
                    use_case_key=use_case_key,
                    evidence=EvidenceDraft(
                        document_id,
                        f"notices[publication-number={notice.number}].winner-name",
                        name[:300],
                        "api_field",
                        VERSION,
                        True,
                    ),
                    url=url,
                    provenance=ProvenanceClass.OFFICIAL,
                    confidence="medium",
                    run_id=ctx.run_id,
                )
                out.append((company_id, notice))
    return out


def ingest_notices(
    ctx: Ctx, notices: list[Notice], document_id: int, use_case_key: str
) -> list[tuple[int, Notice]]:
    """Auftraggeber als ``public_buyer`` mit Bedarfssignal speichern (nie Kandidaten)."""
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
