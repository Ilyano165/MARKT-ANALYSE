"""UK Companies House Public Data API (kostenloser Schlüssel, Basic Auth mit Schlüssel als
Benutzername und leerem Passwort).

Genutzt: ``GET /search/companies`` (Suche) und ``GET /company/{nummer}`` (Profil:
Name, Status, SIC-Codes, Sitz, Gründungsdatum). Keine Mitarbeiter- oder Umsatzzahlen
(die API liefert diese nicht strukturiert).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from .. import repo
from ..context import Ctx
from ..domain import ProvenanceClass
from .common import json_object

SOURCE = "companies_house"
BASE = "https://api.company-information.service.gov.uk"


@dataclass(frozen=True, slots=True)
class UkCompany:
    """Ausgewertete Firmendaten."""

    number: str
    name: str
    status: str
    city: str | None
    postal_code: str | None
    street: str | None
    sic_codes: tuple[str, ...]
    created: str | None


def _auth(ctx: Ctx) -> dict[str, str] | None:
    key = ctx.settings.companies_house_key
    if not key:
        return None
    token = base64.b64encode(f"{key}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def parse_company(item: dict[str, Any]) -> UkCompany | None:
    """Suchtreffer oder Profil auswerten."""
    number = str(item.get("company_number") or "").strip()
    name = str(item.get("company_name") or item.get("title") or "").strip()
    if not number or not name:
        return None
    address = item.get("registered_office_address") or item.get("address") or {}
    if not isinstance(address, dict):
        address = {}
    return UkCompany(
        number=number,
        name=name,
        status=str(item.get("company_status") or "unknown"),
        city=address.get("locality"),
        postal_code=address.get("postal_code"),
        street=address.get("address_line_1"),
        sic_codes=tuple(str(c) for c in item.get("sic_codes") or []),
        created=item.get("date_of_creation"),
    )


def search(ctx: Ctx, term: str, limit: int) -> tuple[list[UkCompany], int | None]:
    """Firmensuche nach Namen."""
    headers = _auth(ctx)
    if headers is None:
        return [], None
    result = ctx.gateway.get(
        SOURCE,
        f"{BASE}/search/companies",
        headers=headers,
        params={"q": term, "items_per_page": str(min(limit, 100))},
        purpose=f"Companies-House-Suche „{term}“",
        title=f"Companies House Suche {term}",
    )
    if not result.ok:
        ctx.note(f"Companies House „{term}“ fehlgeschlagen: {result.status.value} {result.error}")
        return [], None
    try:
        items = json_object(ctx, result).get("items") or []
    except ValueError:
        ctx.note("Companies House: Antwort ist kein JSON")
        return [], result.document_id
    return [
        c for c in (parse_company(i) for i in items if isinstance(i, dict)) if c
    ], result.document_id


def fetch_profile(ctx: Ctx, number: str) -> tuple[UkCompany | None, int | None]:
    """Firmenprofil zu einer Registernummer."""
    headers = _auth(ctx)
    if headers is None:
        return None, None
    result = ctx.gateway.get(
        SOURCE,
        f"{BASE}/company/{number}",
        headers=headers,
        purpose=f"Companies-House-Profil {number}",
        title=f"Companies House {number}",
    )
    if not result.ok:
        return None, None
    try:
        data = json_object(ctx, result)
    except ValueError:
        return None, result.document_id
    return (parse_company(data) if isinstance(data, dict) else None), result.document_id


def store(ctx: Ctx, company: UkCompany, document_id: int) -> int:
    """Firma speichern (Registernummer ist starker Identifikator)."""
    status = {"active": "active", "dissolved": "dissolved", "liquidation": "insolvent"}.get(
        company.status, "unknown"
    )
    with ctx.engine.begin() as conn:
        company_id, _ = repo.resolve_or_create(
            conn,
            company.name,
            [("uk_crn", company.number)],
            document_id=document_id,
            country_code="GB",
            legal_name=company.name,
            city=company.city,
            status=status,
        )
        repo.add_alias(conn, company_id, company.name, "legal_name", document_id)
        repo.add_location(
            conn,
            company_id,
            "registered_office",
            country_code="GB",
            city=company.city,
            postal_code=company.postal_code,
            street=company.street,
            document_id=document_id,
            provenance=ProvenanceClass.OFFICIAL,
        )
        for sic in company.sic_codes:
            repo.add_industry(
                conn,
                company_id,
                "sic_uk2007",
                sic,
                f"SIC {sic}",
                document_id,
                ProvenanceClass.OFFICIAL,
            )
    return company_id
