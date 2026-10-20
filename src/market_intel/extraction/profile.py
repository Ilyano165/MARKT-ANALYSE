"""Profilangaben aus Unternehmensseiten (regelbasiert, mit wörtlichem Zitat).

* strukturierte Daten (schema.org JSON-LD ``Organization`` u. a.): Namen, Marken, Adressen mit
  Koordinaten, Tätigkeitsgebiet, Angebote, Mitarbeiterzahl
* Text: Länder in Sätzen über Standorte/Vertrieb, Reichweitenangaben („in über 40 Ländern“),
  Vertriebswege, Zielgruppen, Standortarten
* Überschriften auf Produkt-/Leistungsseiten als Produktgruppen-Kandidaten

Alles ist ``company_reported`` mit Vertrauen „low“ (außer strukturierte Daten: „medium“).
Ländernamen: i18n-iso-countries (MIT), Sprachen de/en.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources
from typing import Any

from .numbers import sentences

_LD_JSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_ORG_TYPES = {
    "Organization",
    "Corporation",
    "LocalBusiness",
    "Store",
    "AutomotiveBusiness",
    "HomeAndConstructionBusiness",
    "ProfessionalService",
    "OnlineStore",
}
_AMBIGUOUS = {"US", "U.S.", "Georgia", "Jersey", "Chad", "Guinea", "Niger", "Turkey"}


@cache
def country_names() -> dict[str, list[str]]:
    """ISO-Alpha-2 → Namen (de/en)."""
    raw = resources.files("market_intel.data").joinpath("country_names.json").read_text("utf-8")
    data: dict[str, list[str]] = json.loads(raw)["names"]
    return data


@cache
def _country_regex() -> tuple[re.Pattern[str], dict[str, str]]:
    lookup: dict[str, str] = {}
    for code, names in country_names().items():
        for name in names:
            if name in _AMBIGUOUS or (len(name) < 3 and name != "UK"):
                continue
            lookup[name] = code
            if name.endswith("e") and " " not in name:  # „in den Niederlanden“
                lookup.setdefault(name + "n", code)
            if name.startswith("Vereinigte "):
                lookup.setdefault("Vereinigten " + name[len("Vereinigte ") :], code)
    names_sorted = sorted(lookup, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w-])(" + "|".join(re.escape(n) for n in names_sorted) + r")(?![\w-])"
    )
    return pattern, lookup


def countries_in(text: str) -> list[tuple[str, str]]:
    """(ISO-Code, gefundener Name) in Reihenfolge des Auftretens, ohne Doppelungen."""
    pattern, lookup = _country_regex()
    seen: dict[str, str] = {}
    for match in pattern.finditer(text):
        code = lookup[match.group(1)]
        seen.setdefault(code, match.group(1))
    return list(seen.items())


@cache
def lexicon() -> dict[str, dict[str, list[str]]]:
    raw = resources.files("market_intel.data").joinpath("profile_lexicon.toml").read_text("utf-8")
    data: dict[str, dict[str, list[str]]] = tomllib.loads(raw)
    return data


def _matches(text: str, patterns: list[str], whole_word: bool = False) -> bool:
    if whole_word:
        patterns = [rf"(?<!\w)(?:{p})(?!\w)" for p in patterns]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ---------------------------------------------------------------------- strukturierte Daten


@dataclass(frozen=True, slots=True)
class Place:
    """Adresse aus JSON-LD (Koordinaten nur, wenn angegeben)."""

    name: str
    location_type: str
    country: str | None
    city: str | None
    postal_code: str | None
    street: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    quote: str


@dataclass(slots=True)
class OrgData:
    """Angaben einer Organisation aus schema.org-Daten."""

    names: list[str]
    legal_name: str | None
    brands: list[str]
    places: list[Place]
    area_served: list[tuple[str, str]]
    offers: list[str]
    employees: Decimal | None
    parent: str | None
    sub_organizations: list[str]


def _walk(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        out.append(data)
        for value in data.values():
            out.extend(_walk(value))
    elif isinstance(data, list):
        for value in data:
            out.extend(_walk(value))
    return out


def _types(item: dict[str, Any]) -> set[str]:
    kind = item.get("@type")
    return {str(k) for k in (kind if isinstance(kind, list) else [kind]) if k}


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _country_code(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return text.upper()
    found = countries_in(text)
    return found[0][0] if found else None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _place(item: dict[str, Any], location_type: str, raw: str) -> Place | None:
    address = item.get("address") if "address" in item else item
    if isinstance(address, list):
        address = address[0] if address else None
    if not isinstance(address, dict):
        return None
    city = _text(address.get("addressLocality"))
    country = _country_code(address.get("addressCountry"))
    if not city and not country:
        return None
    raw_geo = item.get("geo")
    geo: dict[str, Any] = raw_geo if isinstance(raw_geo, dict) else {}
    lat, lon = _decimal(geo.get("latitude")), _decimal(geo.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    quote = city or country or ""
    if quote not in raw:
        return None
    return Place(
        name=_text(item.get("name")) or "",
        location_type=location_type,
        country=country,
        city=city,
        postal_code=_text(address.get("postalCode")),
        street=_text(address.get("streetAddress")),
        latitude=lat,
        longitude=lon,
        quote=quote,
    )


def _site_type(name: str, default: str) -> str:
    for kind, patterns in lexicon()["location_type"].items():
        if _matches(name, patterns, whole_word=True):
            return kind
    return default


def organization_data(html: str) -> OrgData | None:
    """Erste Organisation aus JSON-LD; Zitate sind Werte, die im Quelltext vorkommen."""
    raw = html[:3_000_000]
    for block in _LD_JSON.findall(raw):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in _walk(data):
            if not (_types(item) & _ORG_TYPES):
                continue
            names = [n for n in (_text(item.get("name")),) if n]
            alternate = item.get("alternateName")
            for alt in alternate if isinstance(alternate, list) else [alternate]:
                if _text(alt):
                    names.append(str(_text(alt)))
            brands = []
            brand = item.get("brand")
            for b in brand if isinstance(brand, list) else [brand]:
                if _text(b):
                    brands.append(str(_text(b)))
            places = []
            main = _place(item, "headquarters", raw)
            if main:
                places.append(main)
            for key in ("location", "department", "subOrganization"):
                value = item.get(key)
                for sub in value if isinstance(value, list) else [value]:
                    if isinstance(sub, dict):
                        default = "branch" if key != "location" else "other_site"
                        sub_place = _place(
                            sub, _site_type(_text(sub.get("name")) or "", default), raw
                        )
                        if sub_place:
                            places.append(sub_place)
            served = []
            area = item.get("areaServed")
            for a in area if isinstance(area, list) else [area]:
                text = _text(a)
                code = _country_code(a) if text else None
                if text and code and text in raw:
                    served.append((code, text))
            offers = []
            catalog = item.get("hasOfferCatalog")
            elements = catalog.get("itemListElement") if isinstance(catalog, dict) else catalog
            for entry in _walk(elements) if elements else []:
                if "OfferCatalog" in _types(entry) or "Product" in _types(entry):
                    name = _text(entry.get("name"))
                    if name and name in raw and name not in offers:
                        offers.append(name)
            employees = item.get("numberOfEmployees")
            count = _decimal(employees.get("value") if isinstance(employees, dict) else employees)
            subs = []
            sub_org = item.get("subOrganization")
            for s in sub_org if isinstance(sub_org, list) else [sub_org]:
                if _text(s):
                    subs.append(str(_text(s)))
            return OrgData(
                names=[n for n in names if n in raw],
                legal_name=_text(item.get("legalName")),
                brands=[b for b in brands if b in raw],
                places=places,
                area_served=served,
                offers=offers[:30],
                employees=count if count is not None and 1 <= count <= 5_000_000 else None,
                parent=_text(item.get("parentOrganization")),
                sub_organizations=subs[:30],
            )
    return None


# ---------------------------------------------------------------------- Text


@dataclass(frozen=True, slots=True)
class MarketHint:
    """Land in einem Satz mit Einordnung (physisch, beliefert, erwähnt)."""

    country: str
    name: str
    presence: str  # physical | supplied | mentioned
    location_type: str | None
    quote: str


def market_hints(text: str) -> list[MarketHint]:
    """Länder in Sätzen über Standorte, Vertrieb oder Präsenz (sonst ignoriert)."""
    lex = lexicon()
    out: list[MarketHint] = []
    seen: set[tuple[str, str]] = set()
    for sentence in sentences(text):
        if len(sentence) > 600:
            continue
        found = countries_in(sentence)
        if not found:
            continue
        site_type = next(
            (k for k, pats in lex["location_type"].items() if _matches(sentence, pats, True)),
            None,
        )
        if site_type:
            presence = "physical"
        elif _matches(sentence, lex["market_verbs"]["supplied"]):
            presence = "supplied"
        elif _matches(sentence, lex["market_verbs"]["mentioned"]):
            presence = "mentioned"
        else:
            continue
        for code, name in found:
            if (code, presence) in seen:
                continue
            seen.add((code, presence))
            out.append(MarketHint(code, name, presence, site_type, sentence[:500]))
    return out[:40]


_REACH = re.compile(
    r"(?:in|nach)\s+((?:über|mehr als|rund|ca\.|etwa|knapp|more than|over|about)\s+)?(\d{1,3})\s+"
    r"(Ländern|Länder|Staaten|countries)",
    re.IGNORECASE,
)


def reach_claims(text: str) -> list[tuple[str, str]]:
    """Reichweitenangaben („in über 40 Ländern“) als (Wert, Zitat)."""
    out: list[tuple[str, str]] = []
    for sentence in sentences(text):
        match = _REACH.search(sentence)
        if match:
            prefix = (match.group(1) or "").strip()
            value = f"{prefix + ' ' if prefix else ''}{match.group(2)} Länder"
            out.append((value, sentence[:400]))
    return out[:5]


def lexicon_hits(text: str, section: str) -> list[tuple[str, str]]:
    """(Bezeichnung, Zitat) für Vertriebswege bzw. Zielgruppen."""
    out: list[tuple[str, str]] = []
    entries = lexicon()[section]
    for sentence in sentences(text):
        if len(sentence) > 500:
            continue
        for label, patterns in entries.items():
            if label not in {o[0] for o in out} and _matches(sentence, patterns):
                out.append((label, sentence))
    return out[:15]


_PRODUCT_PAGE = re.compile(
    r"produkt|product|sortiment|l(?:ö|oe)sung|solution|leistung|service|kategorie|category|shop",
    re.IGNORECASE,
)
_GENERIC_HEADINGS = re.compile(
    r"^(produkte?|products?|leistungen|services?|kontakt|contact|news(letter)?|über uns|"
    r"about( us)?|impressum|datenschutz|cookies?|login|warenkorb|cart|menü|menu|suche|search|"
    r"home|start(seite)?|"
    r"downloads?|karriere|jobs?|unsere .*|our .*|mehr erfahren|learn more|alle .*|all .*)$",
    re.IGNORECASE,
)


def product_headings(url: str, title: str, headings: list[str]) -> list[tuple[str, str]]:
    """Überschriften auf Produkt-/Leistungsseiten als (Art, Bezeichnung)."""
    if not _PRODUCT_PAGE.search(url + " " + title):
        return []
    kind = (
        "service"
        if re.search(r"leistung|service|dienstleist", url + " " + title, re.IGNORECASE)
        else "product_group"
    )
    out: list[tuple[str, str]] = []
    for heading in headings:
        heading = " ".join(heading.split())
        words = heading.split()
        if not 3 <= len(heading) <= 60 or len(words) > 6 or _GENERIC_HEADINGS.match(heading):
            continue
        if re.search(r"[.!?:]$|\d{4}|€|%|@", heading):
            continue
        if heading not in [h for _, h in out]:
            out.append((kind, heading))
    return out[:15]
