"""Standorte und Märkte: physische Präsenz, belieferte Regionen und bloße Erwähnungen.

Drei Stufen werden nie vermischt:

1. **physisch** – Standort mit Adresse/Ort laut Quelle (Sitz, Werk, Niederlassung, Lager …)
2. **beliefert/Export** – Quelle sagt, dass das Unternehmen dorthin liefert oder verkauft
3. **erwähnt** – Land wird nur genannt (z. B. Referenzprojekt, Messe, „weltweit“)

Auf der Karte erscheinen Punkte nur mit Koordinaten aus einer Quelle und mindestens
Ortsgenauigkeit; Länder werden nach der stärksten belegten Stufe eingefärbt.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, select

from .. import db, repo

LOCATION_LABELS = {
    "registered_office": "Satzungssitz",
    "headquarters": "Hauptsitz",
    "production": "Produktion",
    "branch": "Niederlassung",
    "sales_office": "Vertriebsbüro",
    "warehouse": "Lager",
    "logistics": "Logistik",
    "r_and_d": "Forschung & Entwicklung",
    "service": "Service",
    "other_site": "sonstiger Standort",
}
PRESENCE_LABELS = {
    "physical": "physischer Standort",
    "supplied": "beliefert",
    "export": "Exportmarkt",
    "mentioned": "nur erwähnt",
}
PRESENCE_RANK = {"physical": 0, "supplied": 1, "export": 1, "mentioned": 2}
MAP_PRECISIONS = ("site", "city")


def footprint(conn: Connection, company_id: int) -> dict[str, Any]:
    """Standorte, Länderübersicht, Kartendaten und Hinweise für ein Unternehmen."""
    locations = repo.locations_of(conn, company_id)
    markets = repo.markets_of(conn, company_id)
    claims = repo.facts_of(conn, company_id, "market_reach_claim")
    countries: dict[str, dict[str, Any]] = {}

    def entry(code: str) -> dict[str, Any]:
        return countries.setdefault(
            code,
            {"country": code, "presence": None, "site_types": [], "sources": [], "notes": []},
        )

    for loc in locations:
        if not loc["country_code"]:
            continue
        item = entry(loc["country_code"])
        _raise(item, "physical")
        label = LOCATION_LABELS.get(loc["location_type"], loc["location_type"])
        if label not in item["site_types"]:
            item["site_types"].append(label)
        item["sources"].append(loc["source_url"])
    for market in markets:
        if not market["country_code"]:
            continue
        item = entry(market["country_code"])
        _raise(item, market["presence_type"])
        item["sources"].append(market["source_url"])
        item["notes"].append(f"{PRESENCE_LABELS[market['presence_type']]}: {market['basis']}")
    precision_rank = {"site": 0, "city": 1}
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for loc in locations:
        if loc["latitude"] is None or loc["geo_precision"] not in MAP_PRECISIONS:
            continue
        key = (loc["location_type"], loc["country_code"], (loc["city"] or "").lower())
        if (
            key not in best
            or precision_rank[loc["geo_precision"]] < precision_rank[best[key]["geo_precision"]]
        ):
            best[key] = loc
    points = [
        {
            "id": loc["id"],
            "lat": float(loc["latitude"]),
            "lon": float(loc["longitude"]),
            "label": LOCATION_LABELS.get(loc["location_type"], loc["location_type"]),
            "city": loc["city"],
            "country": loc["country_code"],
            "precision": loc["geo_precision"],
            "note": loc["geo_note"],
            "source_url": loc["source_url"],
        }
        for loc in best.values()
    ]
    unmapped = [
        loc
        for loc in locations
        if not (loc["latitude"] is not None and loc["geo_precision"] in MAP_PRECISIONS)
    ]
    hints = []
    if locations and not points:
        hints.append(
            "Keine Standortkoordinaten mit Orts- oder Adressgenauigkeit vorhanden – Karte zeigt "
            "nur Länder"
        )
    if unmapped:
        hints.append(
            f"{len(unmapped)} Standort(e) ohne ausreichend genaue Koordinaten: nur in der Liste"
        )
    for country in countries.values():
        country["sources"] = sorted(set(country["sources"]))
        country["presence_label"] = PRESENCE_LABELS[country["presence"]]
    job_locations = _job_ad_locations(conn, company_id)
    return {
        "locations": [_location_dict(loc) for loc in locations],
        "countries": sorted(
            countries.values(), key=lambda c: (PRESENCE_RANK[c["presence"]], c["country"])
        ),
        "map_points": points,
        "claims": [
            {"value": c["value"], "quote": c["quote"], "source_url": c["source_url"]}
            for c in claims
        ],
        "job_ad_locations": job_locations,
        "hints": hints,
    }


def _raise(item: dict[str, Any], presence: str) -> None:
    current = item["presence"]
    if current is None or PRESENCE_RANK[presence] < PRESENCE_RANK[current]:
        item["presence"] = presence


def _location_dict(loc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": loc["id"],
        "type": loc["location_type"],
        "type_label": LOCATION_LABELS.get(loc["location_type"], loc["location_type"]),
        "country": loc["country_code"],
        "city": loc["city"],
        "postal_code": loc["postal_code"],
        "street": loc["street"],
        "status": loc["site_status"],
        "geo_precision": loc["geo_precision"],
        "geo_note": loc["geo_note"],
        "provenance": loc["provenance_class"],
        "captured_at": loc["captured_at"].isoformat() if loc["captured_at"] else None,
        "source_key": loc["source_key"],
        "source_url": loc["source_url"],
        "quote": loc["quote"],
    }


def _job_ad_locations(conn: Connection, company_id: int) -> list[dict[str, Any]]:
    """Arbeitsorte aus Stellenanzeigen – Hinweis auf Präsenz, kein belegter Standort."""
    rows = conn.execute(
        select(db.hiring_observation.c.observation_date, db.hiring_observation.c.locations).where(
            db.hiring_observation.c.company_id == company_id
        )
    ).all()
    seen: dict[str, str] = {}
    for observed, raw in rows:
        for place in json.loads(raw or "[]"):
            if place and (place not in seen or observed.isoformat() > seen[place]):
                seen[place] = observed.isoformat()
    return [{"place": k, "last_seen": v} for k, v in sorted(seen.items())]


def country_set(conn: Connection, company_id: int, presences: tuple[str, ...]) -> set[str]:
    """Länder mit mindestens einer der genannten Präsenzstufen."""
    data = footprint(conn, company_id)
    return {c["country"] for c in data["countries"] if c["presence"] in presences}
