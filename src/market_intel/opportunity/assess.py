"""Bedarfshypothese und Bewertung je Unternehmen und Anwendungsfall.

Die Hypothese wird ausschließlich aus Belegen zusammengesetzt (Zitate mit Quelle und Datum)
und aus der Produktmatrix (Herstellerangaben). Aussagen über tatsächlichen Werkzeugbedarf,
Lieferanten oder Kaufbereitschaft werden NICHT getroffen; wo nichts belegt ist, steht
„unbekannt“. Keine KI-Formulierungen.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from sqlalchemy import Engine, insert, select

from .. import db, repo
from ..intel.footprint import footprint
from ..intel.series import latest_figure
from ..products import Catalog, products_for
from ..usecases import UseCase
from .evidence import EvidenceIndex, Hit
from .growth import GrowthView, assess_growth
from .model import Model, assign_segment

MAX_QUOTES = 6
ZONE_LABELS = {"core": "Kernmarkt", "europe": "Europa", "other": "außerhalb Europas"}
DISCLAIMER = (
    "Ein hoher Score bedeutet nur: nach den gewählten Kriterien interessant für eine weitere "
    "Prüfung. Keine Verkaufsprognose."
)


def _level(count: int, levels: list[list[float]]) -> float:
    value = 0.0
    for threshold, score in levels:
        if count >= threshold:
            value = float(score)
    return value


def _interp(x: float, points: list[list[float]]) -> float:
    """Lineare Interpolation auf log-Skala zwischen Stützstellen."""
    if x <= points[0][0]:
        return float(points[0][1]) * max(x, 1) / points[0][0]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x <= x1:
            t = (math.log(x) - math.log(x0)) / (math.log(x1) - math.log(x0))
            return float(y0 + t * (y1 - y0))
    return float(points[-1][1])


def _unique_terms(hits: list[Hit]) -> list[str]:
    return sorted({h.term.lower() for h in hits})


def _quote_list(hits: list[Hit]) -> list[dict[str, Any]]:
    """Zitate je Dokument zusammenfassen: steht ein Begriff schon in einem gezeigten Zitat,
    wird er dort ergänzt statt das (fast gleiche) Zitat erneut zu zeigen."""
    out: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda h: (not h.document.own, h.document.document_id, h.term)):
        same_doc = [o for o in out if o["document_id"] == hit.document.document_id]
        holder = next((o for o in same_doc if hit.term.lower() in o["quote"].lower()), None)
        if holder is not None:
            if hit.term not in holder["terms"]:
                holder["terms"].append(hit.term)
            continue
        if len(out) >= MAX_QUOTES:
            continue
        item = hit.as_dict()
        item["terms"] = [hit.term]
        out.append(item)
    return out


def _months(since: datetime | None) -> float | None:
    if since is None:
        return None
    now = datetime.now(UTC)
    moment = since if since.tzinfo else since.replace(tzinfo=UTC)
    return (now - moment).days / 30.4


def assess_company(
    engine: Engine,
    company_id: int,
    case: UseCase,
    catalog: Catalog,
    model: Model,
    index: EvidenceIndex | None = None,
    growth: GrowthView | None = None,
) -> dict[str, Any]:
    """Hypothese, drei Teilwerte, Priorität, Segment, Lücken und nächste Schritte."""
    index = index or EvidenceIndex(engine, company_id)
    groups = products_for(catalog, case)
    activity = index.hits(case.website_terms)
    process_terms = tuple(dict.fromkeys(t for g in groups for t in g.process_terms))
    # Prozessbelege dürfen sich mit Tätigkeitsbegriffen überschneiden: beides ist Beleg.
    process = index.hits(process_terms)
    own_activity = [h for h in activity if h.document.own]
    tools_own = index.hits(catalog.own_brand_terms)
    tools_other = index.hits(catalog.other_tool_terms)
    pr = model.section("product_relevance")
    bp = model.section("business_potential")
    dq = model.section("data_quality")

    # ------------------------------------------------------------ Produktrelevanz
    rel_parts: dict[str, dict[str, Any]] = {}
    own_terms = _unique_terms(own_activity)
    all_terms = _unique_terms(activity)
    if own_terms:
        score = _level(len(own_terms), pr["activity_levels"])
        basis = (
            f"{len(own_terms)} belegte Tätigkeitsbegriffe in eigenen Unternehmensangaben: "
            + ", ".join(own_terms)
        )
    elif all_terms:
        score = float(pr["third_party_only"])
        basis = "Tätigkeit nur in Drittquellen erwähnt: " + ", ".join(all_terms)
    else:
        score, basis = 0.0, "keine Tätigkeit belegt, die zu diesem Anwendungsfall passt"
    counter = [h for h in index.hits(case.counter_terms) if h.document.own] if all_terms else []
    if counter:
        score = round(score * float(pr["counter_factor"]), 3)
        basis += "; Gegenindiz belegt: " + ", ".join(_unique_terms(counter))
    rel_parts["activity_evidence"] = {"score": score, "basis": basis}
    process_found = _unique_terms(process)
    rel_parts["process_evidence"] = {
        "score": _level(len(process_found), pr["process_levels"]) if all_terms else 0.0,
        "basis": ("Prozessbegriffe belegt: " + ", ".join(process_found))
        if process_found
        else "kein zusätzlicher Prozessbegriff belegt",
    }
    documents = {h.document.document_id for h in activity + process}
    own_docs = {h.document.document_id for h in activity + process if h.document.own}
    if not all_terms:
        quality, qbasis = 0.0, "keine Belege"
    elif own_docs:
        quality = min(1.0, 0.7 + 0.15 * (len(own_docs) - 1))
        qbasis = f"{len(own_docs)} eigene Unternehmensdokument(e), {len(documents)} insgesamt"
    else:
        quality, qbasis = 0.4, f"nur {len(documents)} Drittquelle(n)"
    rel_parts["source_quality"] = {"score": quality, "basis": qbasis}
    status_key = (
        case.evidence_status
        if case.evidence_status == "confirmed"
        else ("abgeleitet" if case.origin == "abgeleitet" else "hypothesis")
    )
    rel_parts["application_status"] = {
        "score": float(pr["application_status"][status_key]) if all_terms else 0.0,
        "basis": {
            "confirmed": "Anwendungsfall vom Vertrieb bestätigt",
            "hypothesis": "Anwendungsfall ist Suchhypothese (nicht bestätigt)",
            "abgeleitet": "Anwendungsfall aus dem Portfolio abgeleitet (nicht bestätigt)",
        }[status_key],
    }
    weights = pr["weights"]
    for key, part in rel_parts.items():
        part["weight"] = weights[key]
        part["contribution"] = round(weights[key] * part["score"] * 100, 1)
    relevance = round(sum(p["contribution"] for p in rel_parts.values())) if all_terms else 0

    # ------------------------------------------------------------ Potenzial
    with engine.connect() as conn:
        company = repo.company_row(conn, company_id)
        growth = growth or assess_growth(
            conn, company_id, bp["growth_thresholds"], int(bp["expansion_window_months"])
        )
        place = footprint(conn, company_id)
        industries = [i["label"] for i in repo.industries_of(conn, company_id)]
    heads = latest_figure(growth.headcount)
    revenue = latest_figure(growth.revenue)
    pot: dict[str, dict[str, Any]] = {}
    size_class = "unknown"
    if heads is not None:
        n = float(heads["value"])
        size_class = (
            "large" if n >= 1000 else "medium" if n >= 250 else "small" if n >= 50 else "micro"
        )
        pot["size"] = {
            "score": round(_interp(n, bp["size_points"]), 3),
            "basis": f"{n:,.0f} Mitarbeiter "
            f"({heads['label']}, {heads['scope']}, {heads['provenance']})".replace(",", "."),
        }
    if growth.status != "unknown":
        pot["growth"] = {"score": float(bp["growth"][growth.status]), "basis": growth.metric_text}
    if growth.expansion != "none":
        names = sorted(
            {
                s.label
                for s in growth.signals
                if s.kind not in ("trend",) and s.direction == "positive"
            }
        )
        pot["expansion"] = {
            "score": float(bp["expansion"][growth.expansion]),
            "basis": f"Expansionssignale ({growth.expansion}): " + ", ".join(names),
        }
    countries = {c["country"] for c in place["countries"] if c["presence"] == "physical"}
    country = company["country_code"] or (sorted(countries)[0] if countries else None)
    if country:
        zone = (
            "core"
            if country in bp["core_countries"]
            else "europe"
            if country in bp["europe_countries"]
            else "other"
        )
        pot["reachability"] = {
            "score": float(bp["reachability"][zone]),
            "basis": f"Sitzland {country} ({ZONE_LABELS[zone]})",
        }
    sites = len(place["locations"])
    strategic_known = heads is not None or sites > 0
    strategic = (
        sites >= int(bp["strategic_min_sites"])
        or (heads is not None and float(heads["value"]) >= float(bp["strategic_min_headcount"]))
        or case.key in bp["strategic_applications"]
    )
    if strategic_known or case.key in bp["strategic_applications"]:
        pot["strategic"] = {
            "score": float(bp["strategic_true"] if strategic else bp["strategic_false"]),
            "basis": (
                f"{sites} belegte Standorte"
                + (f", {float(heads['value']):,.0f} Mitarbeiter".replace(",", ".") if heads else "")
                + (
                    "; Anwendungsfall als Multiplikator/Hersteller markiert"
                    if case.key in bp["strategic_applications"]
                    else ""
                )
            ),
        }
    pweights = bp["weights"]
    potential_missing = [k for k in pweights if k not in pot]
    # Unbekannte Komponenten gehen NEUTRAL ein (weder Strafe noch Bonus). Ein Neugewichten
    # nur über die bekannten Komponenten würde einzelne Angaben (z. B. Sitzland) aufblähen.
    neutral = float(bp.get("unknown_component_value", 0.5))
    known = bool(pot)
    for key in potential_missing:
        pot[key] = {
            "score": neutral,
            "basis": f"unbekannt – neutral angesetzt ({neutral:.1f})",
            "unknown": True,
        }
    for key, part in pot.items():
        part["weight"] = pweights[key]
        part["contribution"] = round(pweights[key] * part["score"] * 100, 1)
    pot = {k: pot[k] for k in pweights}
    potential = round(sum(p["contribution"] for p in pot.values())) if known else None

    # ------------------------------------------------------------ Datenqualität
    key_fields = {
        "Website": bool(company["website_domain"]),
        "Land": bool(country),
        "Mitarbeiterzahl": heads is not None,
        "Umsatz": revenue is not None,
        "Branche": bool(industries),
        "Standorte": sites > 0,
        "Tätigkeitsbeleg (eigene Quelle)": bool(own_terms),
    }
    completeness = sum(key_fields.values()) / len(key_fields)
    age = _months(index.newest)
    recency = (
        0.0 if age is None else next((float(v) for m, v in dq["recency_months"] if age <= m), 0.3)
    )
    signal_bases = [s.basis for s in growth.signals]
    confirmation = (
        sum(1 for b in signal_bases if b in ("confirmed", "derived")) / len(signal_bases)
        if signal_bases
        else (1.0 if own_terms else 0.5)
    )
    qparts = {
        "completeness": {
            "score": round(completeness, 3),
            "basis": f"{sum(key_fields.values())}/{len(key_fields)} Kernangaben bekannt",
        },
        "recency": {
            "score": recency,
            "basis": f"jüngster Beleg vor {age:.0f} Monaten"
            if age is not None
            else "keine Dokumente",
        },
        "confirmation": {
            "score": round(confirmation, 3),
            "basis": f"{sum(1 for b in signal_bases if b != 'unconfirmed')}/"
            f"{len(signal_bases)} Signale bestätigt oder abgeleitet"
            if signal_bases
            else "keine Signale",
        },
    }
    data_quality = round(sum(dq["weights"][k] * v["score"] for k, v in qparts.items()) * 100)

    # ------------------------------------------------------------ Priorität, Segment
    w = model.section("model")["priority_weights"]
    if relevance < int(model.section("model")["min_relevance_for_priority"]):
        priority = 0
        priority_basis = "kein belegter Produkt-Fit → Priorität 0 (Größe und Wachstum zählen nicht)"
    else:
        pot_value = (
            potential
            if potential is not None
            else int(model.section("model")["unknown_potential_value"])
        )
        priority = round(w["product_relevance"] * relevance + w["business_potential"] * pot_value)
        priority_basis = (
            f"{w['product_relevance']} × Produktrelevanz {relevance} + {w['business_potential']} × "
            f"Potenzial {pot_value}"
            + ("" if potential is not None else " (unbekannt, neutral angesetzt)")
        )
    core = case.key in model.section("applications")["core"]
    facts = {
        "product_relevance": relevance,
        "business_potential": potential,
        "data_quality": data_quality,
        "growth_status": growth.status,
        "growth_basis": growth.basis,
        "expansion": growth.expansion,
        "size_class": size_class,
        "core_application": core,
        "application_status": case.evidence_status,
    }
    segment, segment_reasons = assign_segment(model, facts)

    # ------------------------------------------------------------ Hypothese, Lücken, Schritte
    product_list = [
        {
            "key": g.key,
            "name": g.name,
            "status": g.status,
            "processes": list(g.processes),
            "limitations": list(g.limitations),
            "sources": list(g.sources),
            "matched_process_terms": sorted(
                {h.term for h in process if h.term in g.process_terms}
                | {h.term for h in activity if h.term in g.process_terms}
            ),
        }
        for g in groups
    ]
    gaps = [name for name, known in key_fields.items() if not known]
    if growth.status == "unknown":
        gaps.append("Umsatz-/Mitarbeiterentwicklung (keine vergleichbare Reihe)")
    gaps.append(
        "tatsächlicher Werkzeugbedarf, eingesetzte Werkzeuge und Lieferanten "
        "(nicht öffentlich belegt)"
    )
    next_steps = _next_steps(
        company, own_terms, all_terms, heads, revenue, growth, tools_own, tools_other, case
    )
    statement = _statement(
        company["display_name"], case, own_activity or activity, product_list, process
    )
    return {
        "company_id": company_id,
        "company": company["display_name"],
        "use_case": case.key,
        "use_case_name": case.name,
        "use_case_status": case.evidence_status,
        "use_case_origin": case.origin,
        "core_application": core,
        "model_version": model.version,
        "hypothesis": {
            "statement": statement,
            "products": product_list,
            "activity_evidence": _quote_list(activity),
            "process_evidence": _quote_list(process),
            "processes_hypothesis": list(case.processes),
            "rationale_hypothesis": case.rationale,
            "counter_indicators": list(case.counter_indicators),
            "counter_evidence": _quote_list(counter),
            "tool_mentions": {
                "own_brand": _quote_list(tools_own),
                "other_brands": _quote_list(tools_other),
                "note": "Erwähnungen sind nur Hinweise; Lieferbeziehungen und Bedarf "
                "sind nicht belegt.",
            },
        },
        "scores": {
            "product_relevance": relevance,
            "product_relevance_parts": rel_parts,
            "business_potential": potential,
            "business_potential_parts": pot,
            "business_potential_unknown": potential_missing,
            "data_quality": data_quality,
            "data_quality_parts": qparts,
            "priority": priority,
            "priority_basis": priority_basis,
            "disclaimer": DISCLAIMER,
        },
        "segment": {
            "key": segment.key,
            "name": segment.name,
            "description": segment.description,
            "reasons": segment_reasons,
        },
        "facts": facts,
        "key_figures": {"revenue": revenue, "headcount": heads},
        "growth": {
            "status": growth.status,
            "basis": growth.basis,
            "expansion": growth.expansion,
            "text": growth.metric_text,
            "signals": [asdict(s) for s in growth.signals],
        },
        "gaps": gaps,
        "next_steps": next_steps,
    }


def _statement(
    name: str,
    case: UseCase,
    activity: list[Hit],
    products: list[dict[str, Any]],
    process: list[Hit],
) -> str:
    """Hypothese als Satz – nur aus Belegen und Matrix, mit ausdrücklicher Einschränkung."""
    if not activity:
        return (
            f"Für {name} ist keine Tätigkeit belegt, die zum Anwendungsfall „{case.name}“ passt. "
            "Eine Bedarfshypothese wird daher nicht aufgestellt."
        )
    first = activity[0]
    where = "eigener Unternehmensangabe" if first.document.own else "einer Drittquelle"
    product_names = ", ".join(p["name"] for p in products[:3])
    process_text = (
        f" Zusätzlich belegt: {', '.join(sorted({h.term for h in process})[:4])}."
        if process
        else ""
    )
    return (
        f"{name} nennt laut {where} die Tätigkeit „{first.term}“ "
        f"({first.document.url}, abgerufen {first.document.retrieved_at:%d.%m.%Y}).{process_text} "
        f"Laut Herstellerangaben unterstützen die Produktgruppen {product_names} solche "
        f"Arbeitsprozesse. Hypothese: Diese Produktgruppen könnten für {name} relevant sein. "
        "Ob tatsächlich Bedarf besteht, welche Werkzeuge eingesetzt werden und ob Kaufbereitschaft "
        "besteht, ist nicht belegt."
    )


def _next_steps(
    company: dict[str, Any],
    own_terms: list[str],
    all_terms: list[str],
    heads: dict[str, Any] | None,
    revenue: dict[str, Any] | None,
    growth: GrowthView,
    tools_own: list[Hit],
    tools_other: list[Hit],
    case: UseCase,
) -> list[str]:
    steps = []
    if not company["website_domain"]:
        steps.append(
            "Website ermitteln (z. B. Impressum, Firmenliste) und mit mi research "
            "erneut untersuchen"
        )
    if all_terms and not own_terms:
        steps.append(
            "Tätigkeit auf der eigenen Website/den Leistungsseiten des Unternehmens prüfen"
        )
    if not all_terms:
        steps.append(
            "Kein Lead: nur weiterverfolgen, wenn neue Belege für die Tätigkeit auftauchen"
        )
    if heads is None:
        steps.append(
            "Mitarbeiterzahl belegen (Jahresabschluss im Unternehmensregister, mi import-document)"
        )
    if revenue is None:
        steps.append(
            "Umsatz belegen (Jahresabschluss/Geschäftsbericht, mi import-document "
            "oder mi add-figure)"
        )
    if any(s.basis == "unconfirmed" for s in growth.signals):
        steps.append(
            "Unbestätigte Pressehinweise prüfen und bestätigte Ereignisse mit mi add-event erfassen"
        )
    if tools_own:
        steps.append(
            "Yellotools wird bereits erwähnt – klären, ob Händler, Bestandskunde oder Anwender"
        )
    if tools_other:
        steps.append(
            "Andere Werkzeugmarken werden erwähnt – vorhandene Lieferanten im Gespräch klären"
        )
    if case.evidence_status != "confirmed":
        steps.append(
            f"Anwendungsfall „{case.name}“ mit dem Yellotools-Vertrieb bestätigen oder verwerfen"
        )
    steps.append(
        "Konkreten Prozess und eingesetzte Werkzeuge im Erstgespräch erfragen "
        "(nicht öffentlich belegt)"
    )
    return steps


def store_assessment(engine: Engine, result: dict[str, Any], run_id: int | None) -> int:
    """Bewertung speichern (jede Bewertung ist eine neue Zeile – Historie)."""
    scores = result["scores"]
    with engine.begin() as conn:
        return int(
            conn.execute(
                insert(db.opportunity_score).values(
                    company_id=result["company_id"],
                    use_case_key=result["use_case"],
                    model_version=result["model_version"],
                    product_relevance=scores["product_relevance"],
                    business_potential=scores["business_potential"],
                    data_quality=scores["data_quality"],
                    priority_score=scores["priority"],
                    segment=result["segment"]["key"],
                    details=json.dumps(result, ensure_ascii=False, default=str),
                    research_run_id=run_id,
                    assessed_at=db.utcnow(),
                )
            ).inserted_primary_key[0]
        )


def latest_assessments(engine: Engine, company_id: int | None = None) -> list[dict[str, Any]]:
    """Jüngste Bewertung je (Unternehmen, Anwendungsfall)."""
    table = db.opportunity_score
    query = select(table).order_by(table.c.id)
    if company_id is not None:
        query = query.where(table.c.company_id == company_id)
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    with engine.connect() as conn:
        for row in conn.execute(query).mappings():
            latest[(row["company_id"], row["use_case_key"])] = dict(row)
    return sorted(latest.values(), key=lambda r: (-(r["priority_score"] or 0), r["company_id"]))
