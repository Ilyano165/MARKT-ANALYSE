"""Ergebnis je Unternehmen (elf Abschnitte) – als Daten und als Markdown."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from ..intel.profile import build_profile
from ..products import Catalog
from ..usecases import UseCase
from .model import Model
from .run import assess_all_applications, no_fit_summary
from .watch import STATUS_LABELS, history_of

SECTIONS = (
    "Unternehmensprofil",
    "Passende Yellotools-Produkte",
    "Mögliche Anwendungsfälle",
    "Belege für den Produkt-Fit",
    "Umsatz- und Beschäftigungsentwicklung",
    "Belegte Wachstumsindikatoren",
    "Relevante Standorte und Länder",
    "Bewertung mit Einzelbegründungen",
    "Datenlücken und Unsicherheiten",
    "Empfohlene nächste Recherche",
    "Quellen und Datum",
)
BASIS_LABELS = {
    "confirmed": "bestätigt",
    "derived": "abgeleitet",
    "unconfirmed": "unbestätigt (Vermutung)",
    "none": "keine",
}


def opportunity_report(
    engine: Engine,
    company_id: int,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
) -> dict[str, Any]:
    """Alle Anwendungsfälle prüfen und das Ergebnis für die beste Hypothese aufbereiten."""
    results = assess_all_applications(engine, company_id, cases, catalog, model)
    with engine.connect() as conn:
        profile = build_profile(conn, company_id, years=10, as_of=date.today())
    with_fit = [r for r in results if r["scores"]["product_relevance"] > 0]
    best = results[0] if with_fit else no_fit_summary(results, len(cases))
    o = profile["overview"]
    evidence_sources: dict[str, dict[str, Any]] = {}
    for result in with_fit:
        hyp = result["hypothesis"]
        for item in hyp["activity_evidence"] + hyp["process_evidence"] + hyp["counter_evidence"]:
            evidence_sources.setdefault(
                item["url"],
                {
                    "url": item["url"],
                    "retrieved_at": item["retrieved_at"],
                    "source_key": item["source_key"],
                },
            )
    return {
        "company_id": company_id,
        "name": o["display_name"],
        "best": best,
        "applications": with_fit,
        "profile": {
            "official_name": o["official_name"],
            "website": o["website"],
            "country": o["country"],
            "industries": [i["label"] for i in o["industries"]],
            "headquarters": o["headquarters"],
            "latest_revenue": o["latest_revenue"],
            "latest_headcount": o["latest_headcount"],
            "parents": [p["name"] for p in o["parents"]],
        },
        "development": [
            {
                "title": s["title"],
                "total": s["spans"][s["total_index"]] if s["total_index"] is not None else None,
                "rows": [
                    {
                        "label": r["label"],
                        "value": r["point"]["value"] if r["point"] else None,
                        "change_pct": r["change_pct"],
                        "status": r["status"],
                    }
                    for r in s["rows"]
                ],
                "notes": s["notes"],
            }
            for s in profile["finance"]["revenue"] + profile["employees"]["series"]
        ],
        "locations": {
            "countries": profile["locations"]["countries"],
            "sites": profile["locations"]["locations"],
        },
        "sources": {"profile": profile["sources"], "evidence": list(evidence_sources.values())},
        "watch_history": history_of(engine, company_id),
        "generated_at": profile["generated_at"],
    }


def _fig(point: dict[str, Any] | None) -> str:
    if not point:
        return "unbekannt"
    unit = "Personen" if point["unit"] == "persons" else point["unit"]
    value = f"{float(point['value']):,.0f}".replace(",", ".")
    return (
        f"{value} {unit} ({point['label']}, {point['scope_label']}, {point['provenance_label']})"
        f" [{point['source_ref']}]"
    )


def to_markdown(report: dict[str, Any]) -> str:
    """Lesbarer Bericht mit den elf Abschnitten."""
    best = report["best"]
    scores = best["scores"]
    p = report["profile"]
    lines = [
        f"# Opportunity-Analyse: {report['name']}",
        "",
        f"Erstellt {report['generated_at'][:16]} UTC · Modell {best['model_version']} · "
        f"Segment: **{best['segment']['name']}** · Priorität {scores['priority']}",
        "",
        f"> {scores['disclaimer']}",
        "",
        f"## 1. {SECTIONS[0]}",
        "",
        f"- Offizieller Name: {p['official_name'] or 'unbekannt'}",
        f"- Website: {p['website'] or 'unbekannt'} · Land: {p['country'] or 'unbekannt'}",
        f"- Branche: {', '.join(p['industries']) or 'unbekannt'}",
        f"- Umsatz: {_fig(p['latest_revenue'])}",
        f"- Mitarbeiter: {_fig(p['latest_headcount'])}",
        "",
        f"## 2. {SECTIONS[1]}",
        "",
    ]
    if not report["applications"]:
        lines += ["Kein Produkt-Fit belegt – keine Produktgruppe wird vorgeschlagen.", ""]
    for product in best["hypothesis"]["products"] if report["applications"] else []:
        lines.append(
            f"- **{product['name']}** ({product['status']}) – Prozesse: "
            f"{', '.join(product['processes'])}; Grenzen: {'; '.join(product['limitations'])}"
        )
    lines += ["", f"## 3. {SECTIONS[2]}", ""]
    for app in report["applications"]:
        s = app["scores"]
        lines.append(
            f"- {app['use_case_name']} ({'Kern' if app['core_application'] else 'neue Branche'}, "
            f"{app['use_case_status']}): Produktrelevanz {s['product_relevance']}, "
            f"Priorität {s['priority']}"
        )
    if not report["applications"]:
        lines.append("- keiner belegt")
    lines += ["", f"## 4. {SECTIONS[3]}", "", best["hypothesis"]["statement"], ""]
    for item in best["hypothesis"]["activity_evidence"] + best["hypothesis"]["process_evidence"]:
        lines.append(
            f"- „{item['quote'][:240]}“ – {item['url']} (abgerufen {item['retrieved_at'][:10]}, "
            f"{'eigene Angabe' if item['own_source'] else 'Drittquelle'})"
        )
    for item in best["hypothesis"]["counter_evidence"]:
        lines.append(f"- Gegenindiz: „{item['quote'][:200]}“ – {item['url']}")
    mentions = best["hypothesis"]["tool_mentions"]
    for item in mentions["own_brand"] + mentions["other_brands"]:
        lines.append(
            f"- Werkzeug-Erwähnung „{item['term']}“: „{item['quote'][:160]}“ – {item['url']}"
        )
    lines += ["", f"## 5. {SECTIONS[4]}", ""]
    if not report["development"]:
        lines.append("- keine Reihe belegt (unbekannt, nicht Stillstand)")
    for series in report["development"]:
        values = ", ".join(
            f"{r['label']}: {r['value'] if r['value'] is not None else 'unbekannt'}"
            for r in series["rows"]
        )
        total = series["total"]
        lines.append(
            f"- {series['title']}: {values}"
            + (f" · {total['formula']}" if total and total["formula"] else "")
        )
    lines += ["", f"## 6. {SECTIONS[5]}", ""]
    growth = best["growth"]
    lines.append(
        f"Status: {growth['status']} (Grundlage: {BASIS_LABELS[growth['basis']]}); Expansion: "
        f"{BASIS_LABELS.get(growth['expansion'], growth['expansion'])}"
    )
    for signal in growth["signals"]:
        lines.append(
            f"- [{BASIS_LABELS[signal['basis']]}] {signal['label']}: {signal['text']}"
            + (f" (Ereignis {signal['date']})" if signal["date"] else "")
            + (f" – {signal['source_url']}" if signal["source_url"] else "")
        )
    lines += ["", f"## 7. {SECTIONS[6]}", ""]
    for country in report["locations"]["countries"]:
        lines.append(
            f"- {country['country']}: {country['presence_label']} "
            f"{', '.join(country['site_types'])}"
        )
    if not report["locations"]["countries"]:
        lines.append("- keine belegt")
    lines += ["", f"## 8. {SECTIONS[7]}", ""]
    lines.append(f"Priorität {scores['priority']} = {scores['priority_basis']}")
    for title, key in (
        ("Produktrelevanz", "product_relevance"),
        ("Geschäftliches Potenzial", "business_potential"),
        ("Datenqualität", "data_quality"),
    ):
        value = scores[key]
        lines.append(f"- **{title}: {value if value is not None else 'unbekannt'}**")
        for name, part in scores[f"{key}_parts"].items():
            weight = part.get("weight")
            lines.append(
                f"  - {name}: {part['score']}"
                + (f" × Gewicht {weight}" if weight is not None else "")
                + f" – {part['basis']}"
            )
    if scores["business_potential_unknown"]:
        lines.append(
            f"- Potenzial unbekannt für: {', '.join(scores['business_potential_unknown'])}"
        )
    lines.append(f"- Segment: {best['segment']['name']} – {'; '.join(best['segment']['reasons'])}")
    lines += ["", f"## 9. {SECTIONS[8]}", ""]
    lines += [f"- {gap}" for gap in best["gaps"]]
    lines += ["", f"## 10. {SECTIONS[9]}", ""]
    lines += [f"- {step}" for step in best["next_steps"]]
    lines += ["", f"## 11. {SECTIONS[10]}", ""]
    for source in report["sources"]["evidence"]:
        lines.append(
            f"- {source['url']} (abgerufen {source['retrieved_at'][:10]}, {source['source_key']})"
        )
    for source in report["sources"]["profile"]:
        lines.append(
            f"- [{source['ref']}] {source['source_name']}: {source['url']} "
            f"(abgerufen {(source['retrieved_at'] or '')[:10]})"
        )
    if report["watch_history"]:
        lines += ["", "### Watchlist", ""]
        for entry in report["watch_history"]:
            lines.append(
                f"- {entry['created_at']:%Y-%m-%d} {entry['watchlist']}: "
                f"{STATUS_LABELS[entry['status']]} – {entry['reason']} ({entry['author']})"
            )
    return "\n".join(lines) + "\n"
