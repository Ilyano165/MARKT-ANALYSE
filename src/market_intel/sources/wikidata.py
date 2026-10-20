"""Wikidata: Identifikatoren (LEI, CIK), Website, Branche, Land, Mitarbeiter- und Umsatzreihen.

Wikidata-Angaben sind ``third_party`` (offene Wissensdatenbank); Fundstelle ist die
gespeicherte EntityData-JSON-Datei, das Zitat ist das exakte JSON-Fragment des Werts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .. import repo
from ..context import Ctx
from ..domain import (
    Confidence,
    EvidenceDraft,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from ..extraction.text import quote_in_text
from .common import as_dict, dict_items, json_object

API = "https://www.wikidata.org/w/api.php"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
SPARQL = "https://query.wikidata.org/sparql"
SOURCE = "wikidata"
VERSION = "wikidata-0.1.0"
_QID = re.compile(r"^Q\d+$")


@dataclass(frozen=True, slots=True)
class WikidataHit:
    """Suchtreffer."""

    qid: str
    label: str
    description: str


def search_entities(ctx: Ctx, text: str, language: str = "de", limit: int = 7) -> list[WikidataHit]:
    """Volltextsuche nach Einträgen (wbsearchentities)."""
    result = ctx.gateway.get(
        SOURCE,
        API,
        params={
            "action": "wbsearchentities",
            "search": text,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": str(limit),
            "format": "json",
        },
        purpose=f"Wikidata-Suche „{text}“",
        title=f"Wikidata-Suche {text}",
    )
    if not result.ok:
        ctx.note(f"Wikidata-Suche „{text}“ fehlgeschlagen: {result.status.value} {result.error}")
        return []
    return [
        WikidataHit(h["id"], h.get("label", ""), h.get("description", ""))
        for h in dict_items(json_object(ctx, result).get("search"))
        if isinstance(h.get("id"), str) and _QID.match(h["id"])
    ]


def load_entity(ctx: Ctx, qid: str) -> tuple[dict[str, Any] | None, int | None, str]:
    """EntityData laden. Gibt (Entity, Dokument-ID, Rohtext) zurück."""
    if not _QID.match(qid):
        return None, None, ""
    result = ctx.gateway.get(
        SOURCE, ENTITY.format(qid=qid), purpose=f"Wikidata {qid}", title=f"Wikidata {qid}"
    )
    if not result.ok:
        ctx.note(f"Wikidata {qid} nicht abrufbar: {result.status.value} {result.error}")
        return None, None, ""
    entities = as_dict(json_object(ctx, result).get("entities"))
    entity = entities.get(qid) or next(iter(entities.values()), None)
    return entity, result.document_id, result.text


def label(entity: dict[str, Any]) -> str:
    """Bevorzugt deutscher, sonst englischer Name."""
    labels = as_dict(entity.get("labels"))
    for lang in ("de", "en"):
        if lang in labels and as_dict(labels[lang]).get("value"):
            return str(labels[lang]["value"])
    for value in labels.values():
        return str(as_dict(value).get("value", ""))
    return str(entity.get("id", ""))


def claim_values(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    """Aussagen einer Eigenschaft ohne ``deprecated``-Rang."""
    return [
        c
        for c in dict_items(as_dict(entity.get("claims")).get(prop))
        if c.get("rank") != "deprecated"
        and isinstance(as_dict(c.get("mainsnak")).get("datavalue"), dict)
    ]


def string_values(entity: dict[str, Any], prop: str) -> list[str]:
    """Zeichenkettenwerte (z. B. LEI, Website)."""
    return [
        str(c["mainsnak"]["datavalue"]["value"])
        for c in claim_values(entity, prop)
        if isinstance(c["mainsnak"]["datavalue"]["value"], str)
    ]


def item_values(entity: dict[str, Any], prop: str) -> list[str]:
    """Verweise auf andere Einträge (QIDs)."""
    out = []
    for c in claim_values(entity, prop):
        value = c["mainsnak"]["datavalue"]["value"]
        if isinstance(value, dict) and _QID.match(str(value.get("id", ""))):
            out.append(str(value["id"]))
    return out


def _point_in_time(claim: dict[str, Any]) -> date | None:
    for qualifier in claim.get("qualifiers", {}).get("P585", []):
        value = qualifier.get("datavalue", {}).get("value", {})
        match = re.match(r"^\+(\d{4})-(\d{2})-(\d{2})", str(value.get("time", "")))
        if not match:
            continue
        year, month, day = (int(g) for g in match.groups())
        precision = int(value.get("precision", 11))
        if precision <= 9 or month == 0:  # nur Jahr bekannt → Jahresende
            return date(year, 12, 31)
        return date(year, month, max(day, 1))
    return None


def _reference_urls(claim: dict[str, Any]) -> list[str]:
    urls = []
    for reference in claim.get("references", []):
        for snak in reference.get("snaks", {}).get("P854", []):
            value = snak.get("datavalue", {}).get("value")
            if isinstance(value, str):
                urls.append(value)
    return urls


_code_cache: dict[tuple[str, str], str | None] = {}


def code_of(ctx: Ctx, qid: str, prop: str) -> str | None:
    """ISO-Code eines Eintrags (P297 Land, P498 Währung); wird nachgeladen, nicht geraten."""
    key = (qid, prop)
    if key not in _code_cache:
        entity, _, _ = load_entity(ctx, qid)
        values = string_values(entity, prop) if entity else []
        _code_cache[key] = values[0].upper() if values else None
    return _code_cache[key]


def quantity_observations(
    ctx: Ctx, company_id: int, entity: dict[str, Any], document_id: int, raw: str
) -> list[ObservationDraft]:
    """Mitarbeiter (P1128) und Umsatz (P2139) mit Stichtag (P585) als Entwürfe."""
    drafts: list[ObservationDraft] = []
    specs = (("P1128", "employee", "headcount_total"), ("P2139", "financial", "revenue"))
    for prop, kind, metric in specs:
        for index, claim in enumerate(claim_values(entity, prop)):
            value = as_dict(claim["mainsnak"]["datavalue"].get("value"))
            amount = str(value.get("amount", ""))
            period_end = _point_in_time(claim)
            if not amount or period_end is None:
                continue  # ohne Stichtag ist die Angabe nicht zeitlich einordbar
            found = re.search(r'"amount"\s*:\s*"' + re.escape(amount) + '"', raw)
            if found is None or not quote_in_text(found.group(0), raw):
                continue
            quote = found.group(0)
            currency = None
            unit = "persons"
            if kind == "financial":
                unit_qid = str(value.get("unit", "")).rsplit("/", 1)[-1]
                currency = code_of(ctx, unit_qid, "P498") if _QID.match(unit_qid) else None
                if not currency:
                    continue
                unit = currency
            refs = _reference_urls(claim)
            drafts.append(
                ObservationDraft(
                    company_id=company_id,
                    kind=kind,
                    metric=metric,
                    value=Decimal(amount.lstrip("+")),
                    unit=unit,
                    currency=currency,
                    period_type=PeriodType.FISCAL_YEAR
                    if kind == "financial"
                    else PeriodType.POINT_IN_TIME,
                    period_start=None,
                    period_end=period_end,
                    scope=Scope.UNKNOWN,
                    provenance_class=ProvenanceClass.THIRD_PARTY,
                    confidence=Confidence.MEDIUM if refs else Confidence.LOW,
                    evidence=EvidenceDraft(
                        document_id, f"claims.{prop}[{index}]", quote, "api_field", VERSION, True
                    ),
                    limitations=("Wikidata-Referenz: " + refs[0])
                    if refs
                    else "Wikidata-Aussage ohne Einzelnachweis",
                )
            )
    return drafts


def ingest_entity(ctx: Ctx, qid: str, *, company_id: int | None = None) -> int | None:
    """Eintrag laden und speichern. Zuordnung nur über starke IDs oder übergebene Firma."""
    entity, document_id, raw = load_entity(ctx, qid)
    if entity is None or document_id is None:
        return None
    leis = string_values(entity, "P1278")
    ciks = [c.lstrip("0") for c in string_values(entity, "P5531")]
    websites = string_values(entity, "P856")
    countries = item_values(entity, "P17")
    country = code_of(ctx, countries[0], "P297") if countries else None
    identifiers = [("wikidata", qid), *[("lei", v) for v in leis], *[("sec_cik", v) for v in ciks]]
    with ctx.engine.begin() as conn:
        if company_id is None:
            company_id, _ = repo.resolve_or_create(
                conn,
                label(entity),
                identifiers,
                document_id=document_id,
                country_code=country,
                website=websites[0] if websites else None,
            )
        else:
            for scheme, value in identifiers:
                if not repo.add_identifier(conn, company_id, scheme, value, document_id):
                    ctx.note(
                        f"Identifikator {scheme}={value} gehört bereits einer anderen Firma; "
                        "nicht zusammengeführt (Prüfung nötig)"
                    )
            repo.fill_company_fields(
                conn, company_id, country_code=country, website=websites[0] if websites else None
            )
        repo.add_alias(conn, company_id, label(entity), "wikidata_label", document_id)
    industry_ids = item_values(entity, "P452")
    if industry_ids:
        labels = _labels(ctx, industry_ids)
        with ctx.engine.begin() as conn:
            for iid in industry_ids:
                repo.add_industry(
                    conn,
                    company_id,
                    "wikidata",
                    iid,
                    labels.get(iid, iid),
                    document_id,
                    ProvenanceClass.THIRD_PARTY,
                )
    ingest_profile(ctx, company_id, entity, document_id, raw)
    drafts = quantity_observations(ctx, company_id, entity, document_id, raw)
    with ctx.engine.begin() as conn:
        for draft in drafts:
            repo.ingest_observation(conn, draft, ctx.run_id)
    return company_id


def _labels(ctx: Ctx, qids: list[str]) -> dict[str, str]:
    result = ctx.gateway.get(
        SOURCE,
        API,
        params={
            "action": "wbgetentities",
            "ids": "|".join(qids[:50]),
            "props": "labels",
            "languages": "de|en",
            "format": "json",
        },
        purpose="Wikidata-Bezeichnungen",
    )
    if not result.ok:
        return {}
    entities = as_dict(json_object(ctx, result).get("entities"))
    return {qid: label(as_dict(e)) for qid, e in entities.items()}


def companies_by_industry(
    ctx: Ctx, industry_qids: list[str], country_iso2: str, limit: int = 60
) -> tuple[list[dict[str, str]], int | None]:
    """Firmen mit Branche (P452) in einem Land (über P297 des Landes, nichts fest verdrahtet)."""
    if not industry_qids or not re.fullmatch(r"[A-Z]{2}", country_iso2):
        return [], None
    values = " ".join(f"wd:{q}" for q in industry_qids if _QID.match(q))
    query = (
        "SELECT DISTINCT ?item ?itemLabel ?website ?lei WHERE { "
        f"VALUES ?industry {{ {values} }} ?item wdt:P452 ?industry ; wdt:P17 ?country . "
        f'?country wdt:P297 "{country_iso2}" . '
        "OPTIONAL { ?item wdt:P856 ?website } OPTIONAL { ?item wdt:P1278 ?lei } "
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en". } } '
        f"LIMIT {int(limit)}"
    )
    result = ctx.gateway.get(
        SOURCE,
        SPARQL,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        purpose="Wikidata-SPARQL Firmen nach Branche",
        title="Wikidata SPARQL Branche",
    )
    if not result.ok:
        ctx.note(f"Wikidata-SPARQL fehlgeschlagen: {result.status.value} {result.error}")
        return [], None
    rows = []
    for binding in dict_items(as_dict(json_object(ctx, result).get("results")).get("bindings")):
        qid = binding.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        if _QID.match(qid):
            rows.append(
                {
                    "qid": qid,
                    "label": binding.get("itemLabel", {}).get("value", qid),
                    "website": binding.get("website", {}).get("value", ""),
                    "lei": binding.get("lei", {}).get("value", ""),
                }
            )
    return rows, result.document_id


# ---------------------------------------------------------------------- Phase 3: Profil

# Eigenschaften (IDs nach Wikidata-Dokumentation; live in dieser Umgebung ungeprüft):
# P159 Hauptsitz, P625 Koordinaten, P17 Staat, P1056 Produkt, P1716 Marke,
# P355 Tochtergesellschaft, P749 Mutterorganisation, P2541 Tätigkeitsgebiet.
LIMITS = {"P1056": 20, "P1716": 20, "P355": 25, "P749": 3, "P2541": 15, "P159": 2}
_place_cache: dict[str, tuple[str, str | None, Decimal | None, Decimal | None]] = {}


def _coordinates(claims: list[dict[str, Any]]) -> tuple[Decimal | None, Decimal | None]:
    for claim in claims:
        value = as_dict(as_dict(claim.get("datavalue")).get("value"))
        if not value:
            value = as_dict(as_dict(as_dict(claim.get("mainsnak")).get("datavalue")).get("value"))
        lat, lon = value.get("latitude"), value.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            return Decimal(str(lat)), Decimal(str(lon))
    return None, None


def place_of(ctx: Ctx, qid: str) -> tuple[str, str | None, Decimal | None, Decimal | None]:
    """(Name, Land, Breite, Länge) eines Ortes; Koordinaten nur, wenn Wikidata sie hat."""
    if qid not in _place_cache:
        entity, _, _ = load_entity(ctx, qid)
        if entity is None:
            _place_cache[qid] = (qid, None, None, None)
        else:
            countries = item_values(entity, "P17")
            country = code_of(ctx, countries[0], "P297") if countries else None
            lat, lon = _coordinates(claim_values(entity, "P625"))
            _place_cache[qid] = (label(entity), country, lat, lon)
    return _place_cache[qid]


def ingest_profile(
    ctx: Ctx, company_id: int, entity: dict[str, Any], document_id: int, raw: str
) -> None:
    """Hauptsitz, Produkte, Marken, Konzernbeziehungen und Tätigkeitsgebiet (third_party)."""
    provenance = ProvenanceClass.THIRD_PARTY

    def ev(prop: str, index: int, qid: str) -> EvidenceDraft:
        found = re.search(r'"id"\s*:\s*"' + re.escape(qid) + '"', raw)
        quote = found.group(0) if found else qid
        return EvidenceDraft(
            document_id, f"claims.{prop}[{index}]", quote, "api_field", VERSION, found is not None
        )

    hq_claims = claim_values(entity, "P159")[: LIMITS["P159"]]
    for index, claim in enumerate(hq_claims):
        value = as_dict(claim["mainsnak"]["datavalue"].get("value"))
        qid = str(value.get("id", ""))
        evidence = ev("P159", index, qid)
        if not _QID.match(qid) or not evidence.quote_verified:
            continue
        name, country, lat, lon = place_of(ctx, qid)
        site_lat, site_lon = _coordinates(claim.get("qualifiers", {}).get("P625", []))
        precision, note = "none", ""
        if site_lat is not None:
            lat, lon, precision = site_lat, site_lon, "site"
            note = "Koordinaten des Hauptsitzes laut Wikidata (Qualifikator P625)"
        elif lat is not None:
            precision = "city"
            note = f"Koordinaten des Ortes {name} (Wikidata), nicht der Adresse"
        with ctx.engine.begin() as conn:
            evidence_id = repo.add_evidence(conn, evidence)
            repo.add_location(
                conn,
                company_id,
                "headquarters",
                country_code=country,
                city=name,
                postal_code=None,
                street=None,
                document_id=document_id,
                provenance=provenance,
                latitude=lat,
                longitude=lon,
                geo_precision=precision,
                geo_note=note,
                evidence_id=evidence_id,
                run_id=ctx.run_id,
            )
    for prop, fact_type in (("P1056", "product_group"), ("P1716", "brand")):
        qids = item_values(entity, prop)[: LIMITS[prop]]
        if not qids:
            continue
        names = _labels(ctx, qids)
        with ctx.engine.begin() as conn:
            for index, qid in enumerate(qids):
                repo.add_profile_fact(
                    conn,
                    company_id,
                    fact_type,
                    names.get(qid, qid),
                    ev(prop, index, qid),
                    provenance=provenance,
                    confidence="medium",
                    method="api_field",
                    run_id=ctx.run_id,
                )
    for prop, direction in (("P355", "child"), ("P749", "parent")):
        qids = item_values(entity, prop)[: LIMITS[prop]]
        if not qids:
            continue
        names = _labels(ctx, qids)
        with ctx.engine.begin() as conn:
            for index, qid in enumerate(qids):
                if not ev(prop, index, qid).quote_verified:
                    continue
                other_id, _ = repo.resolve_or_create(
                    conn, names.get(qid, qid), [("wikidata", qid)], document_id=document_id
                )
                parent, child = (
                    (company_id, other_id) if direction == "child" else (other_id, company_id)
                )
                repo.add_parent(
                    conn,
                    parent,
                    child,
                    "wikidata_parent",
                    f"wikidata_{prop}",
                    document_id,
                    provenance,
                )
    areas = item_values(entity, "P2541")[: LIMITS["P2541"]]
    if areas:
        names = _labels(ctx, areas)
        for index, qid in enumerate(areas):
            code = code_of(ctx, qid, "P297")
            with ctx.engine.begin() as conn:
                repo.add_market(
                    conn,
                    company_id,
                    country_code=code,
                    region_label="" if code else names.get(qid, qid),
                    presence_type="supplied",
                    basis=f"Wikidata Tätigkeitsgebiet (P2541): {names.get(qid, qid)}",
                    evidence=ev("P2541", index, qid),
                    provenance=provenance,
                    confidence="medium",
                    run_id=ctx.run_id,
                )
