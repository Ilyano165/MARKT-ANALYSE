"""Bedarfshypothese und Bewertung je Unternehmen und Anwendungsfall.

Die Hypothese wird ausschließlich aus Belegen zusammengesetzt (Zitate mit Quelle und Datum)
und aus der Produktmatrix (Herstellerangaben). Aussagen über tatsächlichen Werkzeugbedarf,
Lieferanten oder Kaufbereitschaft werden NICHT getroffen; wo nichts belegt ist, steht
„unbekannt“. Keine KI-Formulierungen.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from sqlalchemy import Engine, insert, select

from .. import db, repo
from ..intel.footprint import footprint
from ..intel.series import latest_figure
from ..products import Catalog, matrix_status, products_for
from ..resolution import STRONG_SCHEMES
from ..usecases import UseCase
from .evidence import EvidenceIndex, Hit
from .growth import GrowthView, assess_growth
from .model import Model, assign_segment
from .outcomes import current_outcome, outcome_text
from .roles import (
    HIT_CLASSES,
    RoleProfile,
    RoleRules,
    classify_roles,
    hit_class,
    load_roles,
    name_tokens,
)
from .watch import customer_lists_of, status_of

MAX_QUOTES = 6
ZONE_LABELS = {"core": "Kernmarkt", "europe": "Europa", "other": "außerhalb Europas"}
# Belegstatus je Anwendungsfall (Phase 6). Der Status deckelt die Produktrelevanz
# (``product_relevance.evidence_caps``): ohne Satzbeleg kein mittlerer, ohne belegte eigene
# Verarbeitung kein hoher Fit. „keine_daten“ ist ausdrücklich KEIN „kein Bedarf“.
FIT_STATUS = {
    "belegt": "Tätigkeit in einem Satz belegt und eigene Verarbeitung/Installation belegt",
    "hinweis": "Tätigkeit in einem eigenen Satz genannt, eigene Verarbeitung/Installation nicht "
    "belegt – manuelle Prüfung",
    "widerspruechlich": "Tätigkeit belegt, aber Gegenbeleg vorhanden – manuelle Prüfung",
    "unklar": "nur Produktnennung, Menüpunkt oder Drittquelle – manuelle Prüfung erforderlich",
    "kein_beleg": "eigene Angaben vorhanden, aber keine passende Tätigkeit genannt",
    "keine_daten": "keine eigenen Unternehmensangaben verfügbar – Eignung unbekannt, nicht "
    "ausgeschlossen",
}
REGISTER_SOURCES = ("gleif", "sec_edgar", "companies_house", "handelsregister")
IDENTITY = {
    "register": "Registerkennung automatisch zugeordnet",
    "nutzer_register": "Kennung vom Nutzer vorgegeben und im Register gefunden – die Auswahl "
    "der Gesellschaft hat der Nutzer getroffen",
    "nutzerangabe": "Kennung nur aus einer Nutzerliste, nicht im Register geprüft",
    "ohne_kennung": "nur Name/Website, keine Register- oder Steuerkennung",
}
DATA_STATUS = {
    "eigene_angaben": "eigene Unternehmensangaben abgerufen",
    "website_gesperrt": "Website verweigert den Abruf (Sperre respektiert)",
    "website_fehler": "Website-Abruf fehlgeschlagen",
    "keine_website": "keine Website bekannt",
    "nicht_untersucht": "Website noch nicht untersucht",
}
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


def _quote_list(hits: list[Hit], classes: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """Zitate je Dokument zusammenfassen: steht ein Begriff schon in einem gezeigten Zitat,
    wird er dort ergänzt statt das (fast gleiche) Zitat erneut zu zeigen. Ganze Sätze zuerst."""
    out: list[dict[str, Any]] = []
    rank = {"taetigkeit": 0, "produktbezug": 1, "fragment": 2}
    order = sorted(
        hits,
        key=lambda h: (
            not h.document.own,
            rank.get((classes or {}).get(id(h), ""), 3),
            h.document.document_id,
            h.term,
        ),
    )
    for hit in order:
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
        if classes and id(hit) in classes:
            item["evidence_class"] = classes[id(hit)]
            item["evidence_class_label"] = HIT_CLASSES[classes[id(hit)]]
        out.append(item)
    return out


def _appositions(hits: list[Hit]) -> int:
    """Begriffe, die nur als Klammerzusatz eines anderen Begriffs vorkommen („Lackschutzfolie
    (PPF)“) – derselbe Produktname, kein zweiter Beleg."""
    terms = {h.term for h in hits}
    merged = set()
    for hit in hits:
        for other in terms - {hit.term}:
            pattern = rf"{re.escape(hit.term)}\s*\(\s*{re.escape(other)}\s*\)"
            if re.search(pattern, hit.quote, re.IGNORECASE):
                merged.add(other.lower())
    return len(merged)


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
    roles: RoleProfile | None = None,
    rules: RoleRules | None = None,
) -> dict[str, Any]:
    """Hypothese, drei Teilwerte, Belegstatus, Priorität, Segment, Lücken und nächste Schritte."""
    rules = rules or load_roles()
    index = index or EvidenceIndex(engine, company_id, rules.statement)
    with engine.connect() as conn:
        company = repo.company_row(conn, company_id)
        identity = identity_of(conn, company_id)
        data_status = data_status_of(conn, company_id, index)
    roles = roles or classify_roles(index.texts, company["display_name"], rules)
    tokens = name_tokens(company["display_name"], rules)
    if index.prefer is None:  # je Seite den Satz bevorzugen, der ein Tätigkeitssatz ist
        index.prefer = lambda hit: hit_class(hit, rules, tokens) == "taetigkeit"
    groups = products_for(catalog, case)
    activity = index.hits(case.website_terms)
    process_terms = tuple(dict.fromkeys(t for g in groups for t in g.process_terms))
    process = index.hits(process_terms)
    own_activity = [h for h in activity if h.document.own]
    classes = {id(h): hit_class(h, rules, tokens) for h in activity + process}
    # Direkte Belege: ganzer Satz aus eigener Quelle, kein Produktsatz.
    direct = [h for h in own_activity if classes[id(h)] == "taetigkeit"]
    mentions = [h for h in own_activity if classes[id(h)] != "taetigkeit"]
    # Keine Doppelzählung: Ein Prozessbegriff, der im Tätigkeitsbegriff steckt oder ihn enthält
    # („Vollfolierung“, „Lackschutz“ ↔ „Lackschutzfolie“), ist dasselbe Wort und zählt nur als
    # Tätigkeit. Prozessbelege nur aus Tätigkeitssätzen.
    activity_keys = [t.lower() for t in case.website_terms]
    direct_process = [
        h
        for h in process
        if h.document.own
        and classes[id(h)] == "taetigkeit"
        and not any(h.term.lower() in t or t in h.term.lower() for t in activity_keys)
    ]
    tools_own = index.hits(catalog.own_brand_terms)
    tools_other = index.hits(catalog.other_tool_terms)
    pr = model.section("product_relevance")
    bp = model.section("business_potential")
    dq = model.section("data_quality")

    # ------------------------------------------------------------ Produktrelevanz
    rel_parts: dict[str, dict[str, Any]] = {}
    own_terms = _unique_terms(direct)
    all_terms = _unique_terms(activity)
    if own_terms:
        count = len(own_terms) - _appositions(direct)
        score = _level(count, pr["activity_levels"])
        basis = (
            f"{count} Tätigkeitsbeleg(e) in ganzen Sätzen eigener Unternehmensangaben: "
            + ", ".join(own_terms)
        )
    elif mentions:
        score = float(pr["mention_only"])
        basis = (
            "nur Produktnennung/Menüpunkt in eigenen Angaben, keine Tätigkeit in einem Satz: "
            + ", ".join(_unique_terms(mentions))
        )
    elif all_terms:
        score = float(pr["third_party_only"])
        basis = "Tätigkeit nur in Drittquellen erwähnt: " + ", ".join(all_terms)
    else:
        score, basis = 0.0, "keine Tätigkeit belegt, die zu diesem Anwendungsfall passt"
    counter = [h for h in index.hits(case.counter_terms) if h.document.own] if all_terms else []
    # Verneinte Tätigkeiten („wir bieten keine Fahrzeugfolierung an“) sind Gegenbelege
    counter += [h for h in index.negated_hits(case.website_terms) if h.document.own]
    if counter:
        score = round(score * float(pr["counter_factor"]), 3)
        basis += "; Gegenindiz belegt: " + ", ".join(_unique_terms(counter))
    rel_parts["activity_evidence"] = {"score": score, "basis": basis}
    process_found = _unique_terms(direct_process)
    rel_parts["process_evidence"] = {
        "score": _level(len(process_found), pr["process_levels"]) if direct else 0.0,
        "basis": ("Prozessbegriffe in Sätzen belegt: " + ", ".join(process_found))
        if process_found
        else "kein zusätzlicher Prozessbegriff in einem Satz belegt",
    }
    documents = {h.document.document_id for h in activity + process}
    own_docs = {h.document.document_id for h in direct + direct_process}
    if not all_terms:
        quality, qbasis = 0.0, "keine Belege"
    elif own_docs:
        quality = min(1.0, 0.7 + 0.15 * (len(own_docs) - 1))
        qbasis = (
            f"{len(own_docs)} eigene Unternehmensdokument(e) mit Satzbeleg, "
            f"{len(documents)} insgesamt"
        )
    elif mentions:
        quality = float(pr["mention_quality"])
        qbasis = "nur Produktnennungen/Menüpunkte – kein direkter Beleg"
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
    uncapped = round(sum(p["contribution"] for p in rel_parts.values())) if all_terms else 0
    processing = roles.has("verarbeiter")
    if not all_terms:
        fit_status = "kein_beleg" if data_status == "eigene_angaben" else "keine_daten"
    elif not direct:
        fit_status = "unklar"
    elif counter:
        fit_status = "widerspruechlich"
    else:
        fit_status = "belegt" if processing else "hinweis"
    cap = int(pr["evidence_caps"][fit_status])
    relevance = min(uncapped, cap)

    # ------------------------------------------------------------ Potenzial
    with engine.connect() as conn:
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
    customer_lists = customer_lists_of(
        engine, company_id, list(model.section("crm").get("customer_lists", []))
    )
    watch_status = status_of(engine, company_id)
    outcome = current_outcome(engine, company_id)
    won = bool(outcome and outcome["outcome"] == "gewonnen")
    facts = {
        "is_customer": bool(customer_lists) or won,
        "rejected": bool(watch_status and watch_status["status"] == "verworfen"),
        "product_relevance": relevance,
        "business_potential": potential,
        "data_quality": data_quality,
        "growth_status": growth.status,
        "growth_basis": growth.basis,
        "expansion": growth.expansion,
        "size_class": size_class,
        "core_application": core,
        "application_status": case.evidence_status,
        "fit_status": fit_status,
        "data_status": data_status,
    }
    segment, segment_reasons = assign_segment(model, facts)
    review = _review_reasons(fit_status, roles, identity, data_status, bool(activity))

    # ------------------------------------------------------------ Hypothese, Lücken, Schritte
    product_list = [
        {
            "key": g.key,
            "name": g.name,
            "status": g.status,
            "processes": list(g.processes),
            "limitations": list(g.limitations),
            "sources": list(g.sources),
            "source_checked_at": g.source_checked_at,
            "examples_not_found": list(g.examples_not_found),
            "matched_process_terms": sorted(
                {h.term for h in process if h.term in g.process_terms}
                | {h.term for h in activity if h.term in g.process_terms}
            ),
        }
        for g in groups
    ]
    gaps = [name for name, known in key_fields.items() if not known]
    if customer_lists:
        next_steps_prefix = [f"Bestandskunde (Liste {', '.join(customer_lists)}): Bestandspflege"]
    elif won:
        next_steps_prefix = [f"Kunde ({outcome_text(outcome)}): Bestandspflege"]
    elif facts["rejected"]:
        next_steps_prefix = ["Vom Vertrieb verworfen – nur bei neuen Belegen erneut prüfen"]
    else:
        next_steps_prefix = []
    if growth.status == "unknown":
        gaps.append("Umsatz-/Mitarbeiterentwicklung (keine vergleichbare Reihe)")
    gaps.append(
        "tatsächlicher Werkzeugbedarf, eingesetzte Werkzeuge und Lieferanten "
        "(nicht öffentlich belegt)"
    )
    next_steps = next_steps_prefix + _next_steps(
        company, own_terms, all_terms, heads, revenue, growth, tools_own, tools_other, case
    )
    statement = _statement(
        company["display_name"],
        case,
        direct or own_activity or activity,
        product_list,
        direct_process,
        fit_status,
        processing,
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
            "activity_evidence": _quote_list(activity, classes),
            "process_evidence": _quote_list(process, classes),
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
        "assessment": {
            "fit_status": fit_status,
            "fit_status_label": FIT_STATUS[fit_status],
            "relevance_uncapped": uncapped,
            "cap": cap,
            "capped": uncapped > cap,
            "evidence_classes": {
                **{k: sum(classes[id(h)] == k for h in own_activity) for k in HIT_CLASSES},
                "drittquelle": len(activity) - len(own_activity),
            },
            "processing_evidenced": processing,
            "roles": roles.as_dict(rules),
            "identity": {"key": identity, "label": IDENTITY[identity]},
            "data_status": {"key": data_status, "label": DATA_STATUS[data_status]},
            "manual_review": bool(review),
            "manual_review_reasons": review,
            "product_matrix": matrix_status(catalog),
        },
        "segment": {
            "key": segment.key,
            "name": segment.name,
            "description": segment.description,
            "reasons": segment_reasons,
        },
        "facts": facts,
        "sales_outcome": outcome_text(outcome),
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
    fit_status: str = "belegt",
    processing: bool = True,
) -> str:
    """Hypothese als Satz – nur aus Belegen und Matrix, mit ausdrücklicher Einschränkung."""
    if fit_status == "keine_daten":
        return (
            f"Für {name} liegen keine eigenen Unternehmensangaben vor. Ob eine zum "
            f"Anwendungsfall „{case.name}“ passende Tätigkeit besteht, ist unbekannt – "
            "das ist nicht belegt, aber auch nicht ausgeschlossen."
        )
    if not activity:
        return (
            f"Für {name} ist keine Tätigkeit belegt, die zum Anwendungsfall „{case.name}“ passt. "
            "Eine Bedarfshypothese wird daher nicht aufgestellt."
        )
    first = activity[0]
    if fit_status == "unklar":
        where = "eigenen Angaben" if first.document.own else "einer Drittquelle"
        return (
            f"Zu „{case.name}“ nennt {name} in {where} nur „{first.term}“ als Produktnennung, "
            f"Menüpunkt oder Kategorie („{first.quote[:120]}“, {first.document.url}), aber "
            "keine Tätigkeit in einem Satz. Ob das Unternehmen selbst verarbeitet oder installiert "
            "und ob Werkzeugbedarf besteht, ist nicht belegt – manuelle Prüfung erforderlich."
        )
    where = "eigener Unternehmensangabe" if first.document.own else "einer Drittquelle"
    role_text = (
        " Eigene Verarbeitung/Installation ist mit einem Satzbeleg belegt."
        if processing
        else " Eine eigene Verarbeitung/Installation ist nicht belegt."
    )
    product_names = ", ".join(p["name"] for p in products[:3])
    process_text = (
        f" Zusätzlich belegt: {', '.join(sorted({h.term for h in process})[:4])}."
        if process
        else ""
    )
    return (
        f"{name} nennt laut {where} die Tätigkeit „{first.term}“ "
        f"({first.document.url}, abgerufen {first.document.retrieved_at:%d.%m.%Y})."
        f"{role_text}{process_text} "
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


def _review_reasons(
    fit_status: str, roles: RoleProfile, identity: str, data_status: str, has_hits: bool
) -> list[str]:
    """Warum ein Mensch prüfen sollte, bevor die Bewertung genutzt wird."""
    reasons = []
    if fit_status == "unklar":
        reasons.append(
            "nur Produktnennung, Menüpunkt, Zielgruppen- oder Fremdbezug bzw. Drittquelle – "
            "eigene Tätigkeit nicht in einem Satz belegt"
        )
    if fit_status == "hinweis":
        reasons.append("Tätigkeit genannt, eigene Verarbeitung/Installation nicht belegt")
    if fit_status == "widerspruechlich":
        reasons.append("Tätigkeitsbeleg und Gegenbeleg widersprechen sich")
    if fit_status == "keine_daten":
        reasons.append(f"keine eigenen Angaben ({DATA_STATUS[data_status]}) – Eignung unbekannt")
    if (
        has_hits
        and not roles.has("verarbeiter")
        and (roles.has("hersteller") or roles.has("haendler"))
    ):
        found = [r for r in ("hersteller", "haendler") if roles.has(r)]
        reasons.append(
            f"{' und '.join('Hersteller' if r == 'hersteller' else 'Händler' for r in found)} "
            "ohne belegte eigene Verarbeitung – Werkzeugbedarf unbekannt, nicht ausgeschlossen "
            "(z. B. Anwendungstechnik, Musterbau, Partner-/Kanalrolle prüfen)"
        )
    if identity in ("nutzer_register", "nutzerangabe"):
        reasons.append(f"Firmenzuordnung: {IDENTITY[identity]}")
    return reasons


def identity_of(conn: Any, company_id: int) -> str:
    """Wie sicher ist die Zuordnung zur Gesellschaft? (siehe ``IDENTITY``)."""
    rows = conn.execute(
        select(db.company_identifier.c.scheme, db.source_document.c.source_key)
        .join(
            db.source_document,
            db.source_document.c.id == db.company_identifier.c.source_document_id,
            isouter=True,
        )
        .where(db.company_identifier.c.company_id == company_id)
    ).all()
    register = any(r.source_key in REGISTER_SOURCES for r in rows)
    strong = [r for r in rows if r.scheme in STRONG_SCHEMES and r.scheme != "wikidata"]
    user_given = any(r.source_key == "user_list" for r in strong)
    if register:
        return "nutzer_register" if user_given else "register"
    return "nutzerangabe" if strong else "ohne_kennung"


def data_status_of(conn: Any, company_id: int, index: EvidenceIndex) -> str:
    """Liegen eigene Angaben vor – und wenn nicht, warum nicht? (siehe ``DATA_STATUS``)."""
    if any(document.own for document, _ in index.texts):
        return "eigene_angaben"
    log = db.investigation_log
    row = conn.execute(
        select(log.c.status, log.c.detail)
        .where(log.c.company_id == company_id, log.c.adapter_key == "website")
        .order_by(log.c.id.desc())
    ).first()
    if row is None:
        return "nicht_untersucht"
    if row.status == "failed":
        blocked = any(k in row.detail for k in ("401", "403", "verweigert", "blocked", "robots"))
        return "website_gesperrt" if blocked else "website_fehler"
    return "keine_website" if row.status == "not_applicable" else "nicht_untersucht"


def store_assessment(
    conn: Any, result: dict[str, Any], run_id: int | None, *, assessed_at: datetime | None = None
) -> int:
    """Bewertung speichern (jede Bewertung ist eine neue Zeile – Historie).

    ``conn`` ist eine offene Transaktion (eine Runde wird gemeinsam geschrieben) oder eine
    Engine (Einzelzeile, eigene Transaktion). ``assessed_at`` = Datenstand der Bewertung.
    """
    if isinstance(conn, Engine):
        with conn.begin() as tx:
            return store_assessment(tx, result, run_id, assessed_at=assessed_at)
    scores = result["scores"]
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
                assessed_at=assessed_at or db.utcnow(),
            )
        ).inserted_primary_key[0]
    )


def _batch(row: dict[str, Any]) -> str | None:
    value = json.loads(row["details"] or "{}").get("batch")
    return str(value) if value else None


def latest_assessments(engine: Engine, company_id: int | None = None) -> list[dict[str, Any]]:
    """Jüngste Bewertung je (Unternehmen, Anwendungsfall) – nur aus der jüngsten Bewertungsrunde
    des Unternehmens (ältere Zeilen bleiben als Historie gespeichert, gelten aber nicht mehr)."""
    table = db.opportunity_score
    query = select(table).order_by(table.c.id)
    if company_id is not None:
        query = query.where(table.c.company_id == company_id)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query).mappings()]
    newest: dict[int, str | None] = {}
    for row in rows:
        newest[row["company_id"]] = _batch(row)
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        current = newest[row["company_id"]]
        if current is not None and _batch(row) != current:
            continue  # ältere Runde (auch Bewertungen vor Einführung der Runden)
        latest[(row["company_id"], row["use_case_key"])] = row
    return sorted(latest.values(), key=lambda r: (-(r["priority_score"] or 0), r["company_id"]))
