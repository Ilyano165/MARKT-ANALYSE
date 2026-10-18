"""GLEIF LEI API: rechtlicher Name, Sitz, Registernummer, Mutterbeziehungen (Level 2).

Format: JSON:API (``data[].attributes.entity`` …). Kostenlos, ohne Schlüssel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import repo
from ..context import Ctx
from ..domain import ProvenanceClass
from ..resolution import legal_form_of

BASE = "https://api.gleif.org/api/v1"
SOURCE = "gleif"


@dataclass(frozen=True, slots=True)
class GleifRecord:
    """Ausgewertete LEI-Daten."""

    lei: str
    legal_name: str
    other_names: tuple[str, ...]
    country: str | None
    city: str | None
    postal_code: str | None
    street: str | None
    hq_country: str | None
    hq_city: str | None
    registered_as: str | None
    registration_authority: str | None
    entity_status: str | None


def parse_record(item: dict[str, Any]) -> GleifRecord | None:
    """Einen ``lei-records``-Eintrag auswerten; ``None`` bei unerwartetem Format."""
    attributes = item.get("attributes") or {}
    entity = attributes.get("entity") or {}
    lei = attributes.get("lei") or item.get("id")
    name = (entity.get("legalName") or {}).get("name")
    if not lei or not name:
        return None
    legal = entity.get("legalAddress") or {}
    hq = entity.get("headquartersAddress") or {}
    others = tuple(o.get("name", "") for o in entity.get("otherNames") or [] if o.get("name"))
    lines = legal.get("addressLines") or []
    return GleifRecord(
        lei=str(lei),
        legal_name=str(name),
        other_names=others,
        country=legal.get("country"),
        city=legal.get("city"),
        postal_code=legal.get("postalCode"),
        street=lines[0] if lines else None,
        hq_country=hq.get("country"),
        hq_city=hq.get("city"),
        registered_as=entity.get("registeredAs"),
        registration_authority=(entity.get("registeredAt") or {}).get("id"),
        entity_status=entity.get("status"),
    )


def search(
    ctx: Ctx, query: str, *, country: str | None = None, limit: int = 10
) -> tuple[list[GleifRecord], int | None]:
    """Volltextsuche nach Namen. Gibt Treffer und Dokument-ID zurück."""
    params = {"filter[fulltext]": query, "page[size]": str(limit)}
    if country:
        params["filter[entity.legalAddress.country]"] = country
    result = ctx.gateway.get(
        SOURCE,
        f"{BASE}/lei-records",
        params=params,
        purpose=f"LEI-Suche „{query}“",
        title=f"GLEIF-Suche {query}",
    )
    if not result.ok:
        ctx.note(f"GLEIF-Suche „{query}“ fehlgeschlagen: {result.status.value} {result.error}")
        return [], None
    records = [r for r in (parse_record(i) for i in result.json().get("data") or []) if r]
    return records, result.document_id


def fetch_by_lei(ctx: Ctx, lei: str) -> tuple[GleifRecord | None, int | None]:
    """Datensatz zu einem bekannten LEI (z. B. aus Wikidata) laden."""
    result = ctx.gateway.get(
        SOURCE, f"{BASE}/lei-records/{lei}", purpose=f"LEI {lei}", title=f"GLEIF {lei}"
    )
    if not result.ok:
        ctx.note(f"GLEIF-Datensatz {lei} nicht abrufbar: {result.status.value} {result.error}")
        return None, None
    data = result.json().get("data")
    return (parse_record(data) if isinstance(data, dict) else None), result.document_id


def fetch_parent(ctx: Ctx, lei: str, kind: str) -> tuple[GleifRecord | None, int | None, str]:
    """Direkte (``direct``) oder oberste (``ultimate``) Mutter. 404 = keine gemeldet."""
    result = ctx.gateway.get(
        SOURCE,
        f"{BASE}/lei-records/{lei}/{kind}-parent",
        purpose=f"{kind}-parent {lei}",
        title=f"GLEIF {kind}-parent {lei}",
    )
    if result.http_status == 404:
        return None, None, "keine Mutter gemeldet (oder Meldeausnahme)"
    if not result.ok:
        return None, None, f"Abruf fehlgeschlagen: {result.status.value}"
    data = result.json().get("data")
    return (parse_record(data) if isinstance(data, dict) else None), result.document_id, ""


def store_record(ctx: Ctx, record: GleifRecord, document_id: int) -> int:
    """Firma aus LEI-Daten anlegen bzw. ergänzen; gibt die Firmen-ID zurück."""
    identifiers = [("lei", record.lei)]
    if record.registered_as and record.registration_authority:
        identifiers.append(("gleif_ra", f"{record.registration_authority}:{record.registered_as}"))
    status = {"ACTIVE": "active", "INACTIVE": "dissolved"}.get(
        record.entity_status or "", "unknown"
    )
    with ctx.engine.begin() as conn:
        company_id, _ = repo.resolve_or_create(
            conn,
            record.legal_name,
            identifiers,
            document_id=document_id,
            country_code=record.country,
            legal_name=record.legal_name,
            legal_form=legal_form_of(record.legal_name),
            city=record.city,
            status=status,
        )
        repo.add_alias(conn, company_id, record.legal_name, "legal_name", document_id)
        for other in record.other_names:
            repo.add_alias(conn, company_id, other, "former_or_other_name", document_id)
        repo.add_location(
            conn,
            company_id,
            "registered_office",
            country_code=record.country,
            city=record.city,
            postal_code=record.postal_code,
            street=record.street,
            document_id=document_id,
            provenance=ProvenanceClass.OFFICIAL,
        )
        if record.hq_city and (record.hq_city, record.hq_country) != (record.city, record.country):
            repo.add_location(
                conn,
                company_id,
                "headquarters",
                country_code=record.hq_country,
                city=record.hq_city,
                postal_code=None,
                street=None,
                document_id=document_id,
                provenance=ProvenanceClass.OFFICIAL,
            )
    return company_id


def enrich_parents(ctx: Ctx, company_id: int, lei: str) -> None:
    """Mutterbeziehungen ergänzen (Konsolidierung laut GLEIF Level 2)."""
    for kind, rel in (("direct", "direct_parent"), ("ultimate", "ultimate_parent")):
        parent, doc_id, reason = fetch_parent(ctx, lei, kind)
        if parent is None or doc_id is None:
            ctx.note(f"GLEIF {kind}-parent für {lei}: {reason}")
            continue
        parent_id = store_record(ctx, parent, doc_id)
        with ctx.engine.begin() as conn:
            repo.add_parent(
                conn,
                parent_id,
                company_id,
                rel,
                "accounting_consolidation",
                doc_id,
                ProvenanceClass.OFFICIAL,
            )
