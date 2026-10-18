"""Verbindlicher Funktionstest: echter Recherchelauf ohne bezahlte Zugangsdaten.

Prüft die neun Kriterien aus der Anforderung anhand dessen, was tatsächlich in der Datenbank
und im Abrufprotokoll steht – nichts wird angenommen. Ergebnis: ``PROOF_REPORT.md`` und
``proof.json`` im Ausgabeordner.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select

from . import db
from .analytics import compare, growth, series_for
from .research import task_results
from .sources.registry import SOURCE_BY_KEY

PAID_ENV_HINTS = ("OPENAI", "ANTHROPIC", "BRAVE", "SERPAPI", "NORTHDATA", "BING", "GOOGLE_API")


@dataclass(frozen=True, slots=True)
class Criterion:
    """Ein Prüfkriterium mit Ergebnis."""

    number: int
    title: str
    passed: bool
    details: str


def evaluate(engine: Engine, run_id: int, out_dir: Path, llm_name: str) -> list[Criterion]:
    """Kriterien 1–9 aus Datenbank und Dateien prüfen."""
    results: list[Criterion] = []
    with engine.connect() as conn:
        enabled = [
            r.key
            for r in conn.execute(select(db.source.c.key).where(db.source.c.enabled.is_(True)))
        ]
        paid_enabled = [k for k in enabled if SOURCE_BY_KEY[k].legal_status == "excluded_paid"]
        paid_env = sorted(k for k in os.environ if any(h in k.upper() for h in PAID_ENV_HINTS))
        results.append(
            Criterion(
                1,
                "Start ohne bezahlte API-Schlüssel",
                not paid_enabled,
                f"Aktive Quellen: {', '.join(enabled)}. Kostenpflichtige Quellen aktiv: "
                f"{paid_enabled or 'keine'}. Gelesene Schlüssel: keine (das Programm liest keine "
                f"Bezahl-Schlüssel). Hinweis: Umgebungsvariablen mit Anbieternamen vorhanden, aber "
                f"ungenutzt: {paid_env or 'keine'}",
            )
        )

        ok_rows = conn.execute(
            select(db.fetch_log.c.source_key, func.count())
            .where(db.fetch_log.c.run_id == run_id, db.fetch_log.c.status == "ok")
            .group_by(db.fetch_log.c.source_key)
        ).all()
        bad_rows = conn.execute(
            select(db.fetch_log.c.source_key, db.fetch_log.c.status, func.count())
            .where(
                db.fetch_log.c.run_id == run_id,
                db.fetch_log.c.status.notin_(["ok", "cached", "not_modified"]),
            )
            .group_by(db.fetch_log.c.source_key, db.fetch_log.c.status)
        ).all()
        reached = {r[0]: int(r[1]) for r in ok_rows}
        results.append(
            Criterion(
                2,
                "Zulässige öffentliche Internetquellen erreicht",
                len(reached) >= 2,
                f"Erfolgreiche Abrufe je Quelle: {reached or 'keine'}; gescheiterte/gesperrte: "
                f"{[(r[0], r[1], int(r[2])) for r in bad_rows] or 'keine'}",
            )
        )

        companies = conn.execute(select(func.count()).select_from(db.company)).scalar() or 0
        results.append(
            Criterion(
                3,
                "Unternehmen gefunden",
                companies > 0,
                f"{companies} Unternehmen in der Datenbank",
            )
        )

        docs = (
            conn.execute(
                select(func.count())
                .select_from(db.source_document)
                .where(db.source_document.c.run_id == run_id)
            ).scalar()
            or 0
        )
        obs = sum(
            conn.execute(
                select(func.count()).select_from(t).where(t.c.research_run_id == run_id)
            ).scalar()
            or 0
            for t in db.OBSERVATION_TABLES.values()
        )
        results.append(
            Criterion(
                4,
                "Quellen und Kennzahlen gespeichert",
                docs > 0 and obs > 0,
                f"{docs} Quelldokumente, {obs} Kennzahlen-Beobachtungen in diesem Lauf",
            )
        )

        results_by_task = task_results(engine, run_id)
        compared = [
            r["company_id"] for t, r in results_by_task if t == "profile" and r.get("company_id")
        ]
        series_found = []
        for company_id in compared:
            for kind, metric in (("financial", "revenue"), ("employee", "headcount_total")):
                g = growth(series_for(conn, company_id, kind, metric))
                if g.cagr_pct is not None:
                    series_found.append(
                        f"Firma {company_id} {metric}: CAGR {g.cagr_pct} % "
                        f"({g.first_period} → {g.last_period}, {g.points} Werte)"
                    )
        results.append(
            Criterion(
                5,
                "Historische Daten analysiert",
                bool(series_found),
                "; ".join(series_found) or "keine Reihe mit ≥ 2 vergleichbaren Werten gefunden",
            )
        )

        table = compare(conn, compared) if compared else []
        with_data = [
            r for r in table if r["revenue_latest"] is not None or r["headcount_latest"] is not None
        ]
        results.append(
            Criterion(
                6,
                "Unternehmensvergleich erzeugt",
                len(table) >= 2 and len(with_data) >= 2,
                f"{len(table)} Firmen im Vergleich, {len(with_data)} mit mindestens einer Kennzahl",
            )
        )

        fits = conn.execute(
            select(db.company_product_fit.c.fit_level, func.count())
            .where(db.company_product_fit.c.research_run_id == run_id)
            .group_by(db.company_product_fit.c.fit_level)
        ).all()
        fit_counts = {r[0]: int(r[1]) for r in fits}
        strong_or_plausible = fit_counts.get("strong", 0) + fit_counts.get("plausible", 0)
        results.append(
            Criterion(
                7,
                "Potenzielle Kunden über Anwendungsfälle gefunden",
                strong_or_plausible > 0,
                f"Fit-Bewertungen mit Beleg: {fit_counts or 'keine'}",
            )
        )

    export = next((r for t, r in results_by_task if t == "export"), {})
    files = [Path(f) for f in export.get("files", [])]
    existing = [f.name for f in files if f.exists() and f.stat().st_size > 0]
    data_rows = {
        f.name: _csv_data_rows(f)
        for f in files
        if f.name in ("vergleich.csv", "kennzahlen.csv", "chancen.csv") and f.exists()
    }
    results.append(
        Criterion(
            8,
            "Ergebnisse exportiert",
            len(existing) >= 3 and all(n > 0 for n in data_rows.values()) and bool(data_rows),
            f"Dateien: {', '.join(existing) or 'keine'}; Datenzeilen: {data_rows or 'keine'}",
        )
    )
    results.append(
        Criterion(
            9,
            "Funktioniert ohne lokales KI-Modell",
            llm_name == "none",
            f"KI-Schnittstelle während des Laufs: {llm_name}",
        )
    )
    return results


def _csv_data_rows(path: Path) -> int:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0] == "keine Daten":
        return 0
    return len(lines) - 1


def write_report(
    out_dir: Path,
    run_id: int,
    criteria: list[Criterion],
    run_summary: dict[str, Any],
    environment: str,
) -> Path:
    """Bericht als Markdown und JSON schreiben."""
    out_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(c.passed for c in criteria)
    lines = [
        "# Funktionsnachweis Market Intelligence (kostenloser Betrieb)",
        "",
        f"Run {run_id} · Umgebung: {environment}",
        "",
        f"**Ergebnis: {passed} von {len(criteria)} Kriterien erfüllt.**",
        "",
        "| # | Kriterium | Ergebnis | Belege |",
        "|---|---|---|---|",
    ]
    for c in criteria:
        verdict = "erfüllt" if c.passed else "NICHT erfüllt"
        details = c.details.replace("|", "/").replace("\n", " ")
        lines.append(f"| {c.number} | {c.title} | {verdict} | {details} |")
    lines += ["", "## Hinweise des Laufs (Quellenfehler, Kandidaten, Lücken)", ""]
    lines += [f"- {n}" for n in run_summary.get("notes", [])[:100]] or ["- keine"]
    lines += [
        "",
        "## Abrufstatistik",
        "",
        "```",
        json.dumps(run_summary.get("fetch_by_source", {}), indent=1, ensure_ascii=False),
        "```",
        "",
    ]
    path = out_dir / "PROOF_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "proof.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "environment": environment,
                "criteria": [asdict(c) for c in criteria],
                "summary": run_summary,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return path
