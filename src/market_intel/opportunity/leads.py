"""Lead-Export: qualifizierte Unternehmenslisten (CSV, XLSX, JSON) – ohne Personendaten."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Engine, select

from .. import db, repo
from ..export import _write_csv, safe_cell
from .assess import latest_assessments
from .run import NO_FIT_KEY
from .watch import STATUS_LABELS, status_of

EXCLUDED_SEGMENTS = ("bestandskunde", "verworfen")
LEAD_COLUMNS = (
    "firma",
    "website",
    "land",
    "branche",
    "anwendungsfall",
    "produktgruppen",
    "begruendung",
    "belege",
    "umsatz",
    "mitarbeiter",
    "wachstum",
    "produktrelevanz",
    "potenzial",
    "datenqualitaet",
    "prioritaet",
    "segment",
    "bewertungsstatus",
    "quellenlinks",
    "letzte_recherche",
    "bewertet_am",
    "modell",
    "firma_id",
)


def _figure(point: dict[str, Any] | None) -> str:
    if not point:
        return "unbekannt"
    unit = "Personen" if point["unit"] == "persons" else point["unit"]
    value = f"{float(point['value']):,.0f}".replace(",", ".")
    return f"{value} {unit} ({point['label']}, {point['scope']}, {point['provenance']})"


def lead_rows(
    engine: Engine,
    *,
    include_no_fit: bool = False,
    include_excluded: bool = False,
    min_priority: int = 0,
    segments: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Beste Bewertung je Unternehmen; Unternehmen ohne Produkt-Fit nur auf Wunsch."""
    best: dict[int, dict[str, Any]] = {}
    for row in latest_assessments(engine):
        if row["company_id"] not in best:
            best[row["company_id"]] = row
    rows = []
    for company_id, row in best.items():
        details = json.loads(row["details"])
        if row["use_case_key"] == NO_FIT_KEY and not include_no_fit:
            continue
        if row["segment"] in EXCLUDED_SEGMENTS and not include_excluded:
            continue
        if (row["priority_score"] or 0) < min_priority:
            continue
        if segments and row["segment"] not in segments:
            continue
        with engine.connect() as conn:
            company = repo.company_row(conn, company_id)
            industries = ", ".join(i["label"] for i in repo.industries_of(conn, company_id))
            last_run = conn.execute(
                select(db.research_run.c.finished_at)
                .join(db.run_candidate, db.run_candidate.c.run_id == db.research_run.c.id)
                .where(db.run_candidate.c.company_id == company_id)
                .order_by(db.research_run.c.finished_at.desc())
            ).scalar()
        hyp = details["hypothesis"]
        evidence = hyp["activity_evidence"] + hyp["process_evidence"]
        links = sorted({e["url"] for e in evidence})
        status = status_of(engine, company_id)
        growth = details["growth"]
        rows.append(
            {
                "firma": company["display_name"],
                "website": company["website_domain"] or "",
                "land": company["country_code"] or "",
                "branche": industries,
                "anwendungsfall": details["use_case_name"],
                "produktgruppen": ", ".join(p["name"] for p in hyp["products"])
                if row["use_case_key"] != NO_FIT_KEY
                else "",
                "begruendung": hyp["statement"],
                "belege": " | ".join(
                    f"„{e['quote'][:160]}“ ({e['url']}, {e['retrieved_at'][:10]})"
                    for e in evidence[:4]
                ),
                "umsatz": _figure(details["key_figures"]["revenue"]),
                "mitarbeiter": _figure(details["key_figures"]["headcount"]),
                "wachstum": f"{growth['status']} ({growth['basis']}): {growth['text']}",
                "produktrelevanz": row["product_relevance"],
                "potenzial": row["business_potential"]
                if row["business_potential"] is not None
                else "unbekannt",
                "datenqualitaet": row["data_quality"],
                "prioritaet": row["priority_score"],
                "segment": details["segment"]["name"],
                "bewertungsstatus": STATUS_LABELS[status["status"]]
                if status
                else "automatisch bewertet, nicht geprüft",
                "quellenlinks": " ".join(links),
                "letzte_recherche": last_run.isoformat() if last_run else "",
                "bewertet_am": row["assessed_at"].isoformat(),
                "modell": row["model_version"],
                "firma_id": company_id,
            }
        )
    rows.sort(key=lambda r: (-int(r["prioritaet"] or 0), r["firma"]))
    return rows


def export_leads(engine: Engine, out_dir: Path, **filters: Any) -> list[Path]:
    """leads.csv, leads.xlsx, leads.json schreiben."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = lead_rows(engine, **filters)
    csv_path = out_dir / "leads.csv"
    _write_csv(csv_path, rows)
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "leads"
    sheet.append(list(LEAD_COLUMNS))
    for row in rows:
        sheet.append([safe_cell(row[c]) for c in LEAD_COLUMNS])
    notes = workbook.create_sheet("hinweise")
    for line in (
        "Ein hoher Score bedeutet nur: nach den gewählten Kriterien interessant für eine weitere "
        "Prüfung. Keine Verkaufsprognose.",
        "Bedarf, eingesetzte Werkzeuge, Lieferanten und Kaufbereitschaft sind nicht belegt.",
        "Keine Kontaktdaten von Personen enthalten.",
    ):
        notes.append([line])
    xlsx = out_dir / "leads.xlsx"
    workbook.save(xlsx)
    json_path = out_dir / "leads.json"
    json_path.write_text(
        json.dumps(
            {"erzeugt": db.utcnow().isoformat(), "leads": rows},
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return [csv_path, xlsx, json_path]
