"""Export: CSV, XLSX, JSON, HTML- und Markdown-Bericht. Jede Zahl mit Quelle und Status.

Zellen, die mit ``= + - @`` beginnen, werden entschärft (Schutz vor Formel-Injektion).
"""

from __future__ import annotations

import csv
import html
import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Engine, select

from . import db, repo
from .analytics import compare, data_quality

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Zellwert für CSV/XLSX: Zahlen bleiben Zahlen, gefährliche Texte werden entschärft."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, Decimal):
        return float(value) if abs(value) < Decimal(10) ** 15 else str(value)
    if isinstance(value, int | float):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = str(value)
    return "'" + text if text.startswith(_DANGEROUS) else text


def _rows_observations(engine: Engine, company_ids: Sequence[int]) -> list[dict[str, Any]]:
    out = []
    with engine.connect() as conn:
        for company_id in company_ids:
            name = repo.company_row(conn, company_id)["display_name"]
            for kind in ("financial", "employee"):
                for r in repo.observations(conn, company_id, kind):
                    out.append(
                        {
                            "firma": name,
                            "firma_id": company_id,
                            "art": kind,
                            "kennzahl": r["metric"],
                            "wert": r["value"],
                            "einheit": r["unit"],
                            "waehrung": r["currency"],
                            "zeitraum_beginn": r["period_start"],
                            "zeitraum_ende": r["period_end"],
                            "zeitraumart": r["period_type"],
                            "konsolidierung": r["consolidation_scope"],
                            "herkunftsklasse": r["provenance_class"],
                            "pruefstatus": r["verification_status"],
                            "vertrauen": r["confidence"],
                            "quelle": r["source_key"],
                            "herausgeber": r["publisher"],
                            "quelle_url": r["source_url"],
                            "abgerufen": r["retrieved_at"],
                            "erfasst": r["captured_at"],
                            "fundstelle": r["locator"],
                            "zitat": r["quote"],
                            "methode": r["extraction_method"],
                            "methodik": r["methodology"],
                            "einschraenkungen": r["limitations"],
                        }
                    )
    return out


def _rows_companies(engine: Engine, company_ids: Sequence[int]) -> list[dict[str, Any]]:
    out = []
    with engine.connect() as conn:
        for company_id in company_ids:
            c = repo.company_row(conn, company_id)
            ids = repo.identifiers_of(conn, company_id)
            out.append(
                {
                    "firma_id": company_id,
                    "name": c["display_name"],
                    "rechtlicher_name": c["legal_name"],
                    "rechtsform": c["legal_form"],
                    "land": c["country_code"],
                    "ort": c["registered_city"],
                    "website": c["website_domain"],
                    "status": c["status"],
                    "art": c["entity_kind"],
                    "identifikatoren": "; ".join(f"{k}={v}" for k, v in sorted(ids.items())),
                }
            )
    return out


def _rows_opportunities(engine: Engine, company_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Jüngste Bewertung (Modell v2) je Unternehmen und Anwendungsfall, zerlegt."""
    table = db.opportunity_score
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(table, db.company.c.display_name, db.company.c.website_domain)
                .join(db.company, db.company.c.id == table.c.company_id)
                .where(table.c.company_id.in_(list(company_ids)))
                .order_by(table.c.id)
            )
            .mappings()
            .all()
        )
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    for mapping in rows:
        latest[(mapping["company_id"], mapping["use_case_key"])] = dict(mapping)
    out = []
    for r in sorted(latest.values(), key=lambda x: (-(x["priority_score"] or 0), x["company_id"])):
        details = json.loads(r["details"])
        scores = details["scores"]
        parts = {
            **{f"Relevanz/{k}": v for k, v in scores["product_relevance_parts"].items()},
            **{f"Potenzial/{k}": v for k, v in scores["business_potential_parts"].items()},
        }
        out.append(
            {
                "firma": r["display_name"],
                "firma_id": r["company_id"],
                "website": r["website_domain"],
                "anwendungsfall": r["use_case_key"],
                "segment": details["segment"]["name"],
                "prioritaet": r["priority_score"],
                "produktrelevanz": r["product_relevance"],
                "potenzial": r["business_potential"]
                if r["business_potential"] is not None
                else "unbekannt",
                "datenqualitaet": r["data_quality"],
                "hypothese": details["hypothesis"]["statement"],
                "begruendung": " | ".join(
                    f"{k}: {v['basis']} (Beitrag {v['contribution']})" for k, v in parts.items()
                ),
                "potenzial_unbekannt": ", ".join(scores["business_potential_unknown"]),
                "luecken": "; ".join(details["gaps"]),
                "modell": r["model_version"],
            }
        )
    return out


def _rows_table(engine: Engine, table: Any, where: Any = None) -> list[dict[str, Any]]:
    query = select(table)
    if where is not None:
        query = query.where(where)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query).mappings()]
    for row in rows:
        row.pop("body", None)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if not rows:
            handle.write("keine Daten\n")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: safe_cell(v) for k, v in row.items()})


def export_all(
    engine: Engine,
    out_dir: Path,
    company_ids: Sequence[int],
    compare_ids: Sequence[int],
    run_id: int | None,
) -> list[Path]:
    """Alle Exporte schreiben; gibt die Dateien zurück."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn:
        comparison = compare(conn, list(compare_ids))
        quality = data_quality(conn, list(company_ids))
    sheets: dict[str, list[dict[str, Any]]] = {
        "vergleich": comparison,
        "chancen": _rows_opportunities(engine, company_ids),
        "kennzahlen": _rows_observations(engine, company_ids),
        "firmen": _rows_companies(engine, company_ids),
        "signale": _rows_table(engine, db.business_signal),
        "luecken": _rows_table(engine, db.data_gap),
        "quellenaufrufe": _rows_table(
            engine, db.fetch_log, db.fetch_log.c.run_id == run_id if run_id else None
        ),
        "quellen": _rows_table(engine, db.source),
    }
    files = []
    for name, rows in sheets.items():
        path = out_dir / f"{name}.csv"
        _write_csv(path, rows)
        files.append(path)
    workbook = Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name[:31])
        if rows:
            sheet.append(list(rows[0]))
            for row in rows:
                sheet.append([safe_cell(v) for v in row.values()])
        else:
            sheet.append(["keine Daten"])
    xlsx = out_dir / "market_intel_export.xlsx"
    workbook.save(xlsx)
    files.append(xlsx)
    payload = {
        "erzeugt": db.utcnow().isoformat(),
        "run_id": run_id,
        "datenqualitaet": quality,
        **sheets,
    }
    json_path = out_dir / "market_intel_export.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    files.append(json_path)
    files.append(_write_html(out_dir / "bericht.html", sheets, quality))
    return files


def _write_html(
    path: Path, sheets: dict[str, list[dict[str, Any]]], quality: dict[str, int]
) -> Path:
    def table(rows: list[dict[str, Any]], limit: int = 200) -> str:
        if not rows:
            return "<p><em>keine Daten</em></p>"
        head = "".join(f"<th>{html.escape(str(k))}</th>" for k in rows[0])
        body = "".join(
            "<tr>"
            + "".join(f"<td>{html.escape(str(safe_cell(v)))}</td>" for v in r.values())
            + "</tr>"
            for r in rows[:limit]
        )
        more = (
            f"<p>… {len(rows) - limit} weitere Zeilen im CSV/XLSX</p>" if len(rows) > limit else ""
        )
        return (
            f"<div class=t><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>{more}"
        )

    titles = {
        "vergleich": "Unternehmensvergleich",
        "chancen": "Potenzielle Kunden (Chancen)",
        "kennzahlen": "Kennzahlen mit Quellen",
        "signale": "Signale",
        "luecken": "Dokumentierte Datenlücken",
        "quellenaufrufe": "Quellenaufrufe",
    }
    parts = [
        "<!doctype html><html lang=de><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Market Intelligence Bericht</title><style>",
        "body{font:14px system-ui,sans-serif;margin:16px;color:#1d1d1f;background:#fff}",
        ".t{overflow-x:auto}table{border-collapse:collapse;font-size:12px}",
        "td,th{border:1px solid #ccc;padding:3px 6px;vertical-align:top;text-align:left}",
        "th{background:#f2f2f2}",
        "@media (prefers-color-scheme:dark){body{background:#111;color:#eee}th{background:#222}}",
        "</style></head><body><h1>Market Intelligence – Bericht</h1>",
        f"<p>Erzeugt {html.escape(db.utcnow().isoformat(timespec='seconds'))} UTC. "
        "Alle Werte mit Quelle, Zeitraum und Prüfstatus; „unbekannt“ heißt: gesucht, "
        "nicht gefunden – nicht geschätzt.</p>",
        "<h2>Datenqualität</h2><ul>",
        *(f"<li>{html.escape(k)}: {v}</li>" for k, v in sorted(quality.items())),
        "</ul>",
    ]
    for key, title in titles.items():
        parts.append(f"<h2>{html.escape(title)}</h2>{table(sheets[key])}")
    parts.append("</body></html>")
    path.write_text("".join(parts), encoding="utf-8")
    return path
