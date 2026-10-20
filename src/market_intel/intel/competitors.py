"""Wettbewerbsanalyse: belegte Gemeinsamkeiten statt Marktanteilen.

Je Vergleichsunternehmen werden Dimensionen mit Datenlage ausgewiesen: Produktähnlichkeit,
Zielgruppen, Größe (Umsatz, Mitarbeiter), Standortnetz, internationale Märkte,
Portfoliobreite, belegtes Wachstum. Fehlt eine Seite, heißt die Dimension „unbekannt“ –
sie zählt weder für noch gegen eine Ähnlichkeit.

Einordnung (Regelwerk ``competitors-0.1``):

* **direkt** – mindestens zwei gemeinsame Produktgruppen/Anwendungsfälle (oder Jaccard ≥ 0,3),
  keine widersprechenden Zielgruppen und gemeinsame Länder (Standort oder Belieferung)
* **indirekt** – mindestens eine gemeinsame Produktgruppe/ein Anwendungsfall, oder gleiche
  Branche bei gemeinsamer Zielgruppe (Ersatzlösung für denselben Bedarf)
* **verwandter Markt** – gleiche Branche oder Zielgruppe, aber keine gemeinsame Produktgruppe
* **kein belegter Bezug** / **Datenlage unzureichend**

Es werden **keine Marktanteile** berechnet: Ohne vollständige Marktgröße und vergleichbare
Umsätze wären sie scheinpräzise. Größenvergleiche sind Verhältnisse zweier belegter Werte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, insert, select

from .. import db, repo
from .footprint import country_set
from .series import headcount_series, latest_figure, revenue_series

METHOD = "competitors-0.1"
CLASS_LABELS = {
    "direct": "direkter Wettbewerber",
    "indirect": "indirekter Wettbewerber",
    "related_market": "Unternehmen im verwandten Markt",
    "no_relation": "kein belegter Bezug",
    "insufficient_data": "Datenlage unzureichend",
}
CLASS_RANK = {k: i for i, k in enumerate(CLASS_LABELS)}


@dataclass(slots=True)
class Snapshot:
    """Belegte Merkmale eines Unternehmens für den Vergleich."""

    company_id: int
    name: str
    products: dict[str, str] = field(default_factory=dict)  # normalisiert → Anzeige
    use_cases: set[str] = field(default_factory=set)
    industries: dict[str, str] = field(default_factory=dict)
    targets: dict[str, str] = field(default_factory=dict)
    site_countries: set[str] = field(default_factory=set)
    market_countries: set[str] = field(default_factory=set)
    sites: int = 0
    revenue: dict[str, Any] | None = None
    revenue_eur: dict[str, Any] | None = None
    headcount: dict[str, Any] | None = None
    revenue_cagr: dict[str, Any] | None = None


def snapshot(conn: Connection, company_id: int) -> Snapshot:
    """Merkmale aus der Datenbank (nur belegte Angaben)."""
    company = repo.company_row(conn, company_id)
    snap = Snapshot(company_id, company["display_name"])
    for fact in repo.facts_of(conn, company_id):
        if fact["fact_type"] in ("product_group", "service"):
            snap.products[fact["normalized"]] = fact["value"]
        elif fact["fact_type"] == "target_group":
            snap.targets[fact["normalized"]] = fact["value"]
    fits = conn.execute(
        select(db.company_product_fit.c.use_case_key, db.company_product_fit.c.fit_level).where(
            db.company_product_fit.c.company_id == company_id
        )
    ).all()
    snap.use_cases = {key for key, level in fits if level in ("strong", "plausible")}
    for industry in repo.industries_of(conn, company_id):
        snap.industries[f"{industry['scheme']}:{industry['code']}"] = industry["label"]
    snap.site_countries = country_set(conn, company_id, ("physical",))
    snap.market_countries = country_set(conn, company_id, ("physical", "supplied", "export"))
    snap.sites = len(repo.locations_of(conn, company_id))
    revenue = revenue_series(conn, company_id, years=10)
    snap.revenue = latest_figure(revenue)
    for item in revenue:
        if item["unit"] != "EUR":
            continue
        for row in reversed(item["rows"]):
            if row["point"] is not None:
                candidate = {**row["point"], "label": row["label"]}
                if (
                    snap.revenue_eur is None
                    or candidate["period_end"] > snap.revenue_eur["period_end"]
                ):
                    snap.revenue_eur = candidate
                break
    native = [r for r in revenue if r.get("currency_mode") == "native"]
    for item in native:
        index = item["total_index"]
        if index is not None and item["spans"][index]["cagr_pct"] is not None:
            span = item["spans"][index]
            snap.revenue_cagr = {**span, "series": item["title"]}
            break
    snap.headcount = latest_figure(
        [s for s in headcount_series(conn, company_id) if s["metric"] == "headcount_total"]
    )
    return snap


def _jaccard(a: set[str], b: set[str]) -> Decimal | None:
    if not a or not b:
        return None
    return (Decimal(len(a & b)) / Decimal(len(a | b))).quantize(Decimal("0.01"))


def _ratio(a: dict[str, Any] | None, b: dict[str, Any] | None, unit_key: str) -> dict[str, Any]:
    if a is None or b is None:
        return {"known": False, "text": "unbekannt (Wert fehlt bei mindestens einem Unternehmen)"}
    if a[unit_key] != b[unit_key]:
        return {"known": False, "text": f"nicht vergleichbar ({a[unit_key]} / {b[unit_key]})"}
    base = Decimal(a["value"])
    other = Decimal(b["value"])
    if base <= 0:
        return {"known": False, "text": "Bezugswert ≤ 0"}
    exact = other / base
    ratio = exact.quantize(Decimal("0.1"))
    notes = []
    year_gap = abs(int(a["period_end"][:4]) - int(b["period_end"][:4]))
    if year_gap > 1:
        notes.append(f"Werte liegen {year_gap} Jahre auseinander")
    if a["scope"] != b["scope"]:
        notes.append("unterschiedlicher oder unbekannter Konsolidierungskreis")
    if "derived" in (a["provenance"], b["provenance"]):
        notes.append("enthält umgerechnete Werte")
    word = (
        "deutlich kleiner"
        if exact < Decimal("0.5")
        else "deutlich größer"
        if exact > Decimal(2)
        else "ähnliche Größenordnung"
    )
    return {
        "known": True,
        "ratio": format(ratio, "f"),
        "text": f"{word} ({format(ratio, 'f').replace('.', ',')}-fach; "
        f"{a['label']} vs. {b['label']})" + (f" – {'; '.join(notes)}" if notes else ""),
        "comparable": not notes,
    }


def compare_pair(focal: Snapshot, other: Snapshot) -> dict[str, Any]:
    """Dimensionen und Einordnung für ein Vergleichsunternehmen."""
    own = set(focal.products) | {f"uc:{u}" for u in focal.use_cases}
    theirs = set(other.products) | {f"uc:{u}" for u in other.use_cases}
    shared = own & theirs
    jac = _jaccard(own, theirs)
    shared_labels = sorted(
        focal.products.get(k) or other.products.get(k) or f"Anwendungsfall {k[3:]}" for k in shared
    )
    shared_ind = sorted(set(focal.industries) & set(other.industries))
    targets_known = bool(focal.targets) and bool(other.targets)
    shared_targets = sorted(set(focal.targets) & set(other.targets))
    markets_known = bool(focal.market_countries) and bool(other.market_countries)
    shared_markets = sorted(focal.market_countries & other.market_countries)
    dims: dict[str, Any] = {
        "products": {
            "known": bool(own) and bool(theirs),
            "shared": shared_labels,
            "jaccard": None if jac is None else format(jac, "f"),
            "text": (
                f"{len(shared)} gemeinsame Produktgruppe(n)/Anwendungsfälle"
                + (f" (Jaccard {format(jac, 'f').replace('.', ',')})" if jac is not None else "")
            )
            if own and theirs
            else "unbekannt (Produktangaben fehlen)",
        },
        "targets": {
            "known": targets_known,
            "shared": [focal.targets[k] for k in shared_targets],
            "text": f"{len(shared_targets)} gemeinsame Zielgruppe(n)"
            if targets_known
            else "unbekannt",
        },
        "industries": {
            "known": bool(focal.industries) and bool(other.industries),
            "shared": [focal.industries[k] for k in shared_ind],
            "text": f"{len(shared_ind)} gemeinsame Branchenzuordnung(en)"
            if focal.industries and other.industries
            else "unbekannt",
        },
        "revenue": _ratio(
            focal.revenue_eur or focal.revenue, other.revenue_eur or other.revenue, "unit"
        ),
        "headcount": _ratio(focal.headcount, other.headcount, "unit"),
        "sites": {
            "known": bool(focal.sites) and bool(other.sites),
            "text": f"{focal.sites} / {other.sites} belegte Standorte; gemeinsame Länder mit "
            f"Standort: {', '.join(sorted(focal.site_countries & other.site_countries)) or 'keine'}"
            if focal.sites and other.sites
            else "unbekannt",
        },
        "markets": {
            "known": markets_known,
            "shared": shared_markets,
            "text": f"{len(shared_markets)} gemeinsame Länder (Standort/Belieferung)"
            if markets_known
            else "unbekannt",
        },
        "portfolio": {
            "known": bool(focal.products) and bool(other.products),
            "text": f"{len(focal.products)} / {len(other.products)} belegte Produktgruppen "
            "(Anzahl der Nennungen, keine Bewertung der Breite am Markt)"
            if focal.products and other.products
            else "unbekannt",
        },
        "growth": {
            "known": focal.revenue_cagr is not None and other.revenue_cagr is not None,
            "text": _growth_text(focal.revenue_cagr, other.revenue_cagr),
        },
    }
    classification, reasons = _classify(
        len(shared),
        jac,
        shared_ind,
        targets_known,
        shared_targets,
        markets_known,
        shared_markets,
        own,
        theirs,
    )
    return {
        "competitor_id": other.company_id,
        "name": other.name,
        "classification": classification,
        "classification_label": CLASS_LABELS[classification],
        "reasons": reasons,
        "dimensions": dims,
        "known_dimensions": sum(1 for d in dims.values() if d["known"]),
    }


def _growth_text(a: dict[str, Any] | None, b: dict[str, Any] | None) -> str:
    if a is None or b is None:
        return "unbekannt (keine vergleichbare Umsatzreihe bei mindestens einem Unternehmen)"
    return (
        f"CAGR {a['cagr_pct']} % ({a['start_label']}–{a['end_label']}) vs. "
        f"{b['cagr_pct']} % ({b['start_label']}–{b['end_label']})"
        + (
            "; Zeiträume verschieden"
            if (a["start_label"], a["end_label"]) != (b["start_label"], b["end_label"])
            else ""
        )
    )


def _classify(
    n_shared: int,
    jac: Decimal | None,
    shared_ind: list[str],
    targets_known: bool,
    shared_targets: list[str],
    markets_known: bool,
    shared_markets: list[str],
    own: set[str],
    theirs: set[str],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not own and not theirs and not shared_ind and not shared_targets:
        return "insufficient_data", ["weder Produktangaben noch gemeinsame Branche belegt"]
    target_ok = not targets_known or bool(shared_targets)
    market_ok = not markets_known or bool(shared_markets)
    strong_overlap = n_shared >= 2 or (jac is not None and jac >= Decimal("0.3") and n_shared >= 1)
    if strong_overlap and target_ok and market_ok:
        reasons.append(f"{n_shared} gemeinsame Produktgruppen/Anwendungsfälle")
        if not targets_known:
            reasons.append("Zielgruppen nicht bei beiden belegt")
        if not markets_known:
            reasons.append("Märkte nicht bei beiden belegt – Überschneidung ungeprüft")
        return "direct", reasons
    if n_shared >= 1:
        reasons.append(f"{n_shared} gemeinsame Produktgruppe(n)/Anwendungsfall")
        if not target_ok:
            reasons.append("verschiedene Zielgruppen")
        if not market_ok:
            reasons.append("keine gemeinsamen Länder belegt")
        return "indirect", reasons
    if shared_ind and shared_targets:
        return "indirect", ["gleiche Branche und gemeinsame Zielgruppe, andere Produkte"]
    if shared_ind or shared_targets:
        return "related_market", [
            "gleiche Branche" if shared_ind else "gemeinsame Zielgruppe",
            "keine gemeinsame Produktgruppe belegt",
        ]
    if not own or not theirs:
        return "insufficient_data", ["Produktangaben fehlen bei mindestens einem Unternehmen"]
    return "no_relation", ["keine gemeinsamen Produktgruppen, Branchen oder Zielgruppen belegt"]


def candidate_ids(conn: Connection, company_id: int) -> list[int]:
    """Alle anderen (nicht zusammengeführten) Unternehmen in der Datenbank."""
    rows = conn.execute(
        select(db.company.c.id).where(
            db.company.c.id != company_id, db.company.c.merged_into_id.is_(None)
        )
    ).scalars()
    return [int(r) for r in rows]


def analyze_competitors(
    conn: Connection,
    company_id: int,
    others: list[int] | None = None,
    *,
    store: bool = False,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Vergleich mit allen (oder den genannten) Unternehmen, sortiert nach Nähe."""
    focal = snapshot(conn, company_id)
    results = [
        compare_pair(focal, snapshot(conn, o)) for o in (others or candidate_ids(conn, company_id))
    ]
    results.sort(
        key=lambda r: (
            CLASS_RANK[r["classification"]],
            -len(r["dimensions"]["products"]["shared"]),
            -r["known_dimensions"],
            r["name"],
        )
    )
    if store:
        now = db.utcnow()
        for result in results:
            conn.execute(
                insert(db.competitor_assessment).values(
                    company_id=company_id,
                    competitor_id=result["competitor_id"],
                    classification=result["classification"],
                    dimensions=json.dumps(result["dimensions"], ensure_ascii=False, default=str),
                    rationale="; ".join(result["reasons"]),
                    method_version=METHOD,
                    research_run_id=run_id,
                    assessed_at=now,
                )
            )
    return {
        "company_id": company_id,
        "name": focal.name,
        "method": METHOD,
        "focal": {
            "products": sorted(focal.products.values()),
            "use_cases": sorted(focal.use_cases),
            "targets": sorted(focal.targets.values()),
            "industries": sorted(focal.industries.values()),
            "market_countries": sorted(focal.market_countries),
        },
        "groups": {key: [r for r in results if r["classification"] == key] for key in CLASS_LABELS},
        "note": "Keine Marktanteile: Ohne belegte Marktgröße und vergleichbare Umsätze aller "
        "Anbieter wären Anteile scheinpräzise. Größenangaben sind Verhältnisse zweier belegter "
        "Werte.",
    }
