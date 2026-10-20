"""Unternehmensprofil: alle Abschnitte mit Quellenverweisen, Stand und Herkunft.

Jede Zahl trägt Jahr/Stichtag, Konsolidierungskreis (Konzern/Einzelgesellschaft/unbekannt),
Herkunft (amtlich, Unternehmensangabe, Drittquelle, abgeleitet, Schätzung) und einen
Verweis [Qn] auf das Quellenverzeichnis sowie die Beobachtungs-ID (Rückverfolgung bis zum
Zitat im gespeicherten Dokument). Fehlende Angaben werden als „unbekannt“ geliefert.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import Connection, select

from .. import db, repo
from ..engine.changes import history
from .competitors import CLASS_LABELS
from .footprint import LOCATION_LABELS, footprint
from .series import headcount_series, latest_figure, revenue_series, segments_of

PROVENANCE_LABELS = {
    "official": "amtlich/Pflichtveröffentlichung",
    "company_reported": "Unternehmensangabe",
    "third_party": "Drittquelle",
    "derived": "abgeleitet (berechnet)",
    "estimate": "Schätzung",
}
SCOPE_LABELS = {"group": "Konzern", "single_entity": "Einzelgesellschaft", "unknown": "unbekannt"}
SECTIONS = (
    ("overview", "Überblick"),
    ("finance", "Finanzen"),
    ("employees", "Mitarbeiter"),
    ("locations", "Standorte"),
    ("products", "Produkte"),
    ("markets", "Märkte"),
    ("history", "Historie"),
    ("sources", "Quellen"),
    ("notes", "Interne Notizen"),
)
GROWTH_EVENTS = (
    "investment",
    "new_site",
    "expansion",
    "new_market",
    "new_product_line",
    "hiring",
    "acquisition",
)


class SourceIndex:
    """Vergibt [Qn] je Dokument/URL und sammelt Metadaten fürs Quellenverzeichnis."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.refs: dict[str, str] = {}
        self.entries: list[dict[str, Any]] = []

    def ref(self, url: str | None, source_key: str | None = None) -> str | None:
        if not url:
            return None
        if url not in self.refs:
            key = f"Q{len(self.entries) + 1}"
            self.refs[url] = key
            doc = (
                self.conn.execute(
                    select(
                        db.source_document.c.id,
                        db.source_document.c.source_key,
                        db.source_document.c.title,
                        db.source_document.c.retrieved_at,
                        db.source_document.c.published_at,
                        db.source_document.c.content_type,
                    )
                    .where(db.source_document.c.url == url)
                    .order_by(db.source_document.c.retrieved_at.desc())
                )
                .mappings()
                .first()
            )
            source = None
            skey = (doc["source_key"] if doc else None) or source_key
            if skey:
                source = (
                    self.conn.execute(select(db.source).where(db.source.c.key == skey))
                    .mappings()
                    .first()
                )
            self.entries.append(
                {
                    "ref": key,
                    "url": url,
                    "document_id": doc["id"] if doc else None,
                    "title": doc["title"] if doc else None,
                    "source_key": skey,
                    "source_name": source["name"] if source else skey,
                    "publisher": source["publisher"] if source else None,
                    "license": source["license"] if source else None,
                    "retrieved_at": doc["retrieved_at"].isoformat() if doc else None,
                    "published_at": doc["published_at"].isoformat()
                    if doc and doc["published_at"]
                    else None,
                    "content_type": doc["content_type"] if doc else None,
                }
            )
        return self.refs[url]


def _figure(point: dict[str, Any] | None, sources: SourceIndex) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        **point,
        "scope_label": SCOPE_LABELS.get(point["scope"], point["scope"]),
        "provenance_label": PROVENANCE_LABELS.get(point["provenance"], point["provenance"]),
        "official": point["provenance"] == "official",
        "estimate": point["provenance"] == "estimate",
        "source_ref": sources.ref(point.get("source_url"), point.get("source_key")),
    }


def _annotate_series(series: list[dict[str, Any]], sources: SourceIndex) -> None:
    for item in series:
        for row in item["rows"]:
            if row["point"] is not None:
                row["point"] = _figure(row["point"], sources)
            row["intra_year"] = [_figure(p, sources) for p in row["intra_year"]]
        for excluded in item["excluded"]:
            excluded["point"] = _figure(excluded["point"], sources)


def build_profile(
    conn: Connection,
    company_id: int,
    *,
    years: int = 10,
    include_estimates: bool = False,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Vollständiges Profil für Oberfläche, JSON-Export und Bericht."""
    as_of = as_of or date.today()
    sources = SourceIndex(conn)
    company = repo.company_row(conn, company_id)
    ids = repo.identifiers_of(conn, company_id)
    aliases = repo.aliases_of(conn, company_id)
    facts = repo.facts_of(conn, company_id)
    relations = repo.relations_of(conn, company_id)
    industries = repo.industries_of(conn, company_id)
    place = footprint(conn, company_id)
    revenue = revenue_series(
        conn, company_id, years=years, include_estimates=include_estimates, as_of=as_of
    )
    heads = headcount_series(
        conn, company_id, years=years, include_estimates=include_estimates, as_of=as_of
    )
    _annotate_series(revenue, sources)
    _annotate_series(heads, sources)
    segments = segments_of(conn, company_id)
    segment_series = []
    for segment in segments.get("employee", []):
        items = headcount_series(conn, company_id, years=years, segment=segment, as_of=as_of)
        _annotate_series(items, sources)
        segment_series.extend(items)
    for segment in segments.get("financial", []):
        items = revenue_series(conn, company_id, years=years, segment=segment, as_of=as_of)
        _annotate_series(items, sources)
        segment_series.extend(items)

    def fact_list(kind: str) -> list[dict[str, Any]]:
        return [
            {
                "value": f["value"],
                "confidence": f["confidence"],
                "provenance_label": PROVENANCE_LABELS.get(
                    f["provenance_class"], f["provenance_class"]
                ),
                "method": f["method"],
                "quote": f["quote"],
                "captured_at": f["captured_at"].isoformat(),
                "last_seen_at": f["last_seen_at"].isoformat(),
                "source_ref": sources.ref(f["source_url"], f["source_key"]),
            }
            for f in facts
            if f["fact_type"] == kind
        ]

    for loc in place["locations"]:
        loc["source_ref"] = sources.ref(loc["source_url"], loc["source_key"])
    for point in place["map_points"]:
        point["source_ref"] = sources.ref(point["source_url"])
    for claim in place["claims"]:
        claim["source_ref"] = sources.ref(claim["source_url"])
    markets = []
    for m in repo.markets_of(conn, company_id):
        markets.append(
            {
                "country": m["country_code"],
                "region": m["region_label"],
                "presence": m["presence_type"],
                "basis": m["basis"],
                "quote": m["quote"],
                "confidence": m["confidence"],
                "captured_at": m["captured_at"].isoformat(),
                "source_ref": sources.ref(m["source_url"], m["source_key"]),
            }
        )
    for country in place["countries"]:
        country["source_refs"] = [r for r in (sources.ref(u) for u in country["sources"]) if r]
    hq = [loc for loc in place["locations"] if loc["type"] in ("headquarters", "registered_office")]
    hq.sort(key=lambda loc: 0 if loc["type"] == "headquarters" else 1)
    events = []
    for e in repo.events_of(conn, company_id):
        events.append(
            {
                "id": e["id"],
                "type": e["event_type"],
                "event_date": e["event_date"].isoformat() if e["event_date"] else None,
                "date_precision": e["date_precision"],
                "summary": e["summary"],
                "counterparty": e["counterparty_name"],
                "affected_headcount": e["affected_headcount"],
                "amount": format(e["amount"], "f") if e["amount"] is not None else None,
                "currency": e["currency"],
                "verification": e["verification_status"],
                "confidence": e["confidence"],
                "method": e["method"],
                "quote": e["quote"],
                "learned_at": e["captured_at"].isoformat(),
                "source_ref": sources.ref(e["source_url"], e["source_key"]),
            }
        )
    signals = []
    for s in repo.signals_for(conn, company_id):
        doc_url = conn.execute(
            select(db.source_document.c.url).where(
                db.source_document.c.id == s["source_document_id"]
            )
        ).scalar()
        signals.append(
            {
                "type": s["signal_type"],
                "event_date": s["event_date"].isoformat() if s["event_date"] else None,
                "summary": s["summary"],
                "quote": s["quote"],
                "learned_at": s["captured_at"].isoformat(),
                "source_ref": sources.ref(s["url"] or doc_url),
            }
        )
    timeline = history(conn, company_id)
    for item in timeline:
        if item.get("document_id"):
            url = conn.execute(
                select(db.source_document.c.url).where(
                    db.source_document.c.id == item["document_id"]
                )
            ).scalar()
            item["source_ref"] = sources.ref(url)
    latest_revenue = _figure(latest_figure(revenue), sources) if revenue else None
    latest_heads = _figure(
        latest_figure([s for s in heads if s["metric"] == "headcount_total"])
        or latest_figure(heads),
        sources,
    )
    growth_indicators = _growth_indicators(revenue, heads, events, signals)
    gaps = repo.gaps_of(conn, company_id)
    fits = conn.execute(
        select(db.company_product_fit).where(db.company_product_fit.c.company_id == company_id)
    ).mappings()
    use_cases: dict[str, dict[str, Any]] = {}
    for fit in fits:
        use_cases[fit["use_case_key"]] = {
            "use_case": fit["use_case_key"],
            "level": fit["fit_level"],
            "rationale": fit["rationale"],
            "assessed_at": fit["assessed_at"].isoformat(),
            "evidence_ids": json.loads(fit["evidence_ids"]),
        }
    competitors = conn.execute(
        select(db.competitor_assessment, db.company.c.display_name)
        .join(db.company, db.company.c.id == db.competitor_assessment.c.competitor_id)
        .where(db.competitor_assessment.c.company_id == company_id)
        .order_by(db.competitor_assessment.c.assessed_at.desc())
    ).mappings()
    latest_competitors: dict[int, dict[str, Any]] = {}
    for c in competitors:
        latest_competitors.setdefault(
            c["competitor_id"],
            {
                "competitor_id": c["competitor_id"],
                "name": c["display_name"],
                "classification": c["classification"],
                "label": CLASS_LABELS.get(c["classification"], c["classification"]),
                "rationale": c["rationale"],
                "assessed_at": c["assessed_at"].isoformat(),
            },
        )
    unknown = []
    if latest_revenue is None:
        unknown.append("Umsatz")
    if latest_heads is None:
        unknown.append("Mitarbeiterzahl")
    if not hq:
        unknown.append("Hauptsitz")
    if not fact_list("product_group") and not use_cases:
        unknown.append("Produktgruppen")
    if not markets and not place["countries"]:
        unknown.append("Absatzländer")
    notes = repo.notes_of(conn, company_id)
    return {
        "company_id": company_id,
        "generated_at": db.utcnow().isoformat(),
        "as_of": as_of.isoformat(),
        "years": years,
        "include_estimates": include_estimates,
        "sections": [{"key": k, "label": v} for k, v in SECTIONS],
        "overview": {
            "display_name": company["display_name"],
            "official_name": company["legal_name"],
            "legal_form": company["legal_form"],
            "status": company["status"],
            "country": company["country_code"],
            "website": company["website_domain"],
            "identifiers": ids,
            "names": sorted(
                {(a["alias"], a["alias_type"]) for a in aliases}, key=lambda a: (a[1], a[0])
            ),
            "brands": fact_list("brand"),
            "headquarters": hq[0] if hq else None,
            "other_locations": [loc for loc in place["locations"] if loc not in hq[:1]],
            "parents": [
                {
                    "id": r["other_id"],
                    "name": r["other_name"],
                    "type": r["relationship_type"],
                    "basis": r["basis"],
                    "provenance_label": PROVENANCE_LABELS.get(r["provenance_class"]),
                    "captured_at": r["captured_at"].isoformat(),
                    "source_ref": sources.ref(r["source_url"], r["source_key"]),
                }
                for r in relations["parents"]
            ],
            "subsidiaries": [
                {
                    "id": r["other_id"],
                    "name": r["other_name"],
                    "type": r["relationship_type"],
                    "basis": r["basis"],
                    "provenance_label": PROVENANCE_LABELS.get(r["provenance_class"]),
                    "captured_at": r["captured_at"].isoformat(),
                    "source_ref": sources.ref(r["source_url"], r["source_key"]),
                }
                for r in relations["children"]
            ],
            "industries": [
                {
                    "scheme": i["scheme"],
                    "code": i["code"],
                    "label": i["label"],
                    "provenance_label": PROVENANCE_LABELS.get(i["provenance_class"]),
                    "source_ref": sources.ref(i["source_url"], i["source_key"]),
                }
                for i in industries
            ],
            "sub_industries": fact_list("sub_industry"),
            "latest_revenue": latest_revenue,
            "latest_headcount": latest_heads,
            "growth_indicators": growth_indicators,
            "unknown": unknown,
            "competitors": sorted(
                latest_competitors.values(), key=lambda c: (c["classification"], c["name"])
            ),
        },
        "finance": {
            "revenue": revenue,
            "segments": [s for s in segment_series if s["metric"] == "revenue"],
        },
        "employees": {
            "series": heads,
            "segments": [s for s in segment_series if s["metric"] != "revenue"],
            "principles": [
                "Nettoveränderung ist nicht gleich Einstellungen bzw. Abgänge "
                "(1.000 → 1.100 heißt nicht 100 Einstellungen).",
                "Stellenanzeigen zeigen Personalbedarf, nicht tatsächliche Einstellungen.",
                "Stichtag, Jahresdurchschnitt (§ 267 Abs. 5 HGB) und Vollzeitäquivalente werden "
                "getrennt geführt.",
            ],
        },
        "locations": {**place, "location_labels": LOCATION_LABELS},
        "products": {
            "product_groups": fact_list("product_group"),
            "services": fact_list("service"),
            "brands": fact_list("brand"),
            "use_cases": sorted(use_cases.values(), key=lambda u: u["use_case"]),
        },
        "markets": {
            "countries": place["countries"],
            "entries": markets,
            "sales_channels": fact_list("sales_channel"),
            "target_groups": fact_list("target_group"),
            "claims": place["claims"],
        },
        "history": {"timeline": timeline, "events": events, "signals": signals},
        "gaps": [
            {
                "metric": g["metric"],
                "reason": g["reason"],
                "detail": g["detail"],
                "searched": g["searched_sources"],
                "checked_at": g["checked_at"].isoformat(),
            }
            for g in gaps
        ],
        "notes": [
            {
                "id": n["id"],
                "section": n["section"],
                "body": n["body"],
                "author": n["author"],
                "created_at": n["created_at"].isoformat(),
                "supersedes_id": n["supersedes_id"],
            }
            for n in notes
        ],
        "sources": sources.entries,
    }


def _growth_indicators(
    revenue: list[dict[str, Any]],
    heads: list[dict[str, Any]],
    events: list[dict[str, Any]],
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Belegte Wachstumshinweise – jede Zeile mit Quelle; keine eigene Wertung."""
    out = []
    for item in revenue + heads:
        index = item["total_index"]
        if index is None or item.get("currency_mode") == "converted":
            continue
        span = item["spans"][index]
        if span["cagr_pct"] is None:
            continue
        out.append(
            {
                "kind": "cagr",
                "text": f"{item['title']}: CAGR {str(span['cagr_pct']).replace('.', ',')} % "
                f"({span['start_label']}–{span['end_label']})"
                + (" – nicht organisch" if span["inorganic"] else ""),
                "source_ref": None,
            }
        )
    for event in events:
        if event["type"] in GROWTH_EVENTS:
            out.append(
                {
                    "kind": event["type"],
                    "text": f"{event['event_date'] or 'Datum unbekannt'}: {event['summary'][:200]}",
                    "source_ref": event["source_ref"],
                    "verification": event["verification"],
                }
            )
    for signal in signals[:10]:
        out.append(
            {
                "kind": f"signal:{signal['type']}",
                "text": f"{signal['event_date'] or 'Datum unbekannt'}: {signal['summary'][:200]}",
                "source_ref": signal["source_ref"],
            }
        )
    return out


def observation_detail(conn: Connection, kind: str, observation_id: int) -> dict[str, Any] | None:
    """Rückverfolgung einer Zahl: Beobachtung, Beleg (Zitat, Fundstelle), Dokument, Abruf."""
    table = db.OBSERVATION_TABLES.get(kind)
    if table is None:
        return None
    row = (
        conn.execute(
            select(
                table,
                db.evidence.c.quote,
                db.evidence.c.locator,
                db.evidence.c.extraction_method,
                db.evidence.c.extractor_version,
                db.evidence.c.quote_verified,
                db.evidence.c.calculation,
                db.source_document.c.url.label("source_url"),
                db.source_document.c.source_key,
                db.source_document.c.retrieved_at,
                db.source_document.c.sha256,
                db.source_document.c.title.label("document_title"),
            )
            .join(db.evidence, db.evidence.c.id == table.c.evidence_id)
            .join(db.source_document, db.source_document.c.id == table.c.source_document_id)
            .where(table.c.id == observation_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    detail = dict(row)
    others = [
        dict(r)
        for r in conn.execute(
            select(
                table.c.id,
                table.c.value,
                table.c.unit,
                table.c.provenance_class,
                table.c.verification_status,
                table.c.captured_at,
                db.source_document.c.url.label("source_url"),
            )
            .join(db.source_document, db.source_document.c.id == table.c.source_document_id)
            .where(
                table.c.company_id == row["company_id"],
                table.c.metric == row["metric"],
                table.c.period_end == row["period_end"],
                table.c.segment == row["segment"],
                table.c.id != observation_id,
            )
        ).mappings()
    ]
    derived_from = [int(x) for x in (row["derived_from"] or "").split(",") if x.strip()]
    fetches = [
        dict(r)
        for r in conn.execute(
            select(db.fetch_log)
            .where(db.fetch_log.c.document_id == row["source_document_id"])
            .order_by(db.fetch_log.c.at)
        ).mappings()
    ]
    return {
        "observation": detail,
        "alternatives": others,
        "derived_from": derived_from,
        "fetches": fetches,
        "provenance_label": PROVENANCE_LABELS.get(row["provenance_class"]),
        "scope_label": SCOPE_LABELS.get(row["consolidation_scope"]),
    }
