"""Schritt 8: Ergebnisbericht. Jede Aussage verweist auf eine Quelle [Qn] oder ist als
Berechnung gekennzeichnet. Veröffentlichungsdaten erscheinen nur, wenn sie gespeichert sind.

Ist eine Quelle beim letzten Abruf nicht erreichbar gewesen, wird das vermerkt – die
gespeicherte Beobachtung bleibt unverändert und gilt weder als falsch noch als aktuell.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Engine, func, select

from .. import db, repo
from ..analytics import growth
from ..llm.assistant import Assistant, Fact
from ..usecases import UseCase
from .strategy import SearchStrategy

STEP_NAMES = {
    1: "Suchstrategie",
    2: "Kandidaten finden",
    3: "Quellen untersuchen",
    4: "Informationen extrahieren",
    5: "Unternehmen abgleichen",
    6: "Angaben validieren",
    7: "Ergebnisse speichern",
    8: "Bericht erzeugen",
}


def fmt_number(value: Decimal | float | int | None, unit: str = "") -> str:
    """Deutsche Zahlendarstellung mit Größenordnung."""
    if value is None:
        return "unbekannt"
    v = Decimal(value)
    if unit and unit.isalpha() and len(unit) == 3 and abs(v) >= 1_000_000:
        scaled, word = (
            (v / Decimal(10**9), "Mrd.") if abs(v) >= 10**9 else (v / Decimal(10**6), "Mio.")
        )
        text = f"{scaled:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{text} {word} {unit}"
    text = f"{v:,.0f}".replace(",", ".")
    return f"{text} {unit}".strip()


@dataclass(slots=True)
class Sources:
    """Quellenverzeichnis mit fortlaufenden Nummern."""

    engine: Engine
    numbers: dict[int, int] = field(default_factory=dict)

    def ref(self, document_id: int | None) -> str:
        if document_id is None:
            return ""
        if document_id not in self.numbers:
            self.numbers[document_id] = len(self.numbers) + 1
        return f"[Q{self.numbers[document_id]}]"

    def listing(self) -> list[str]:
        lines = []
        with self.engine.connect() as conn:
            for document_id, number in sorted(self.numbers.items(), key=lambda x: x[1]):
                d = (
                    conn.execute(
                        select(db.source_document).where(db.source_document.c.id == document_id)
                    )
                    .mappings()
                    .first()
                )
                if d is None:
                    continue
                published = d["published_at"].isoformat() if d["published_at"] else "unbekannt"
                retrieved = d["retrieved_at"].strftime("%Y-%m-%d %H:%M UTC")
                reach = _reachability(conn, d["url"], d["retrieved_at"])
                lines.append(
                    f"- **Q{number}** {d['publisher'] or d['source_key']}: {d['title'] or d['url']}"
                    f" – {d['url']} – abgerufen {retrieved}, veröffentlicht {published}, "
                    f"SHA-256 {d['sha256'][:12]}…{reach}"
                )
        return lines


_FAILED = ("network_error", "http_error", "blocked_robots", "blocked_tdm", "too_large")


def _reachability(conn: Any, url: str, retrieved: Any) -> str:
    """Hinweis, wenn die URL oder ihre Website nach dem Abruf nicht mehr erreichbar war."""
    latest = conn.execute(
        select(db.fetch_log.c.status, db.fetch_log.c.at)
        .where(db.fetch_log.c.url == url)
        .order_by(db.fetch_log.c.id.desc())
    ).first()
    if latest is not None and latest.status in _FAILED:
        return (
            f" – ⚠ beim letzten Abruf {latest.at:%Y-%m-%d} nicht erreichbar ({latest.status}); "
            "gespeicherte Angaben unverändert, Aktualität unbekannt"
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    host_latest = conn.execute(
        select(db.fetch_log.c.status, db.fetch_log.c.at, db.fetch_log.c.url)
        .where(db.fetch_log.c.url.like(f"{parts.scheme}://{parts.netloc}/%"))
        .order_by(db.fetch_log.c.id.desc())
    ).first()
    if host_latest is None or host_latest.status not in _FAILED:
        return ""
    at = host_latest.at.replace(tzinfo=None)
    if retrieved is not None and at <= retrieved.replace(tzinfo=None):
        return ""
    return (
        f" – ⚠ Website {parts.netloc} beim letzten Abruf {host_latest.at:%Y-%m-%d} nicht "
        f"erreichbar ({host_latest.status}); gespeicherte Angaben unverändert, Aktualität "
        "unbekannt"
    )


def _run(engine: Engine, run_id: int) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(select(db.research_run).where(db.research_run.c.id == run_id))
            .mappings()
            .one()
        )


def step_overview(engine: Engine, run_id: int) -> list[dict[str, Any]]:
    """Aufgaben je Schritt und Status (auch für die Fortschrittsanzeige)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.research_task.c.step, db.research_task.c.status, func.count())
            .where(db.research_task.c.run_id == run_id)
            .group_by(db.research_task.c.step, db.research_task.c.status)
        ).all()
    by_step: dict[int, dict[str, int]] = defaultdict(dict)
    for step, status, count in rows:
        by_step[int(step)][status] = int(count)
    return [{"step": s, "name": STEP_NAMES.get(s, "?"), **by_step.get(s, {})} for s in range(1, 9)]


def _company_section(
    engine: Engine,
    company_id: int,
    run_id: int,
    strategy: SearchStrategy,
    sources: Sources,
    ai: Assistant,
    cases: dict[str, UseCase],
) -> tuple[list[str], dict[str, Any]]:
    lines: list[str] = []
    facts: list[Fact] = []
    data: dict[str, Any] = {"company_id": company_id}
    with engine.connect() as conn:
        c = repo.company_row(conn, company_id)
        ids = repo.identifiers_of(conn, company_id)
        id_docs = {
            r.scheme: r.source_document_id
            for r in conn.execute(
                select(
                    db.company_identifier.c.scheme, db.company_identifier.c.source_document_id
                ).where(db.company_identifier.c.company_id == company_id)
            )
        }
        lines.append(f"### {c['display_name']}")
        meta = [f"Land: {c['country_code'] or 'unbekannt'}"]
        if c["legal_name"]:
            meta.append(f"rechtlicher Name: {c['legal_name']}")
        if c["website_domain"]:
            meta.append(f"Website: {c['website_domain']}")
        lines.append(" · ".join(meta))
        if ids:
            lines.append(
                "Kennungen: "
                + ", ".join(
                    f"{k}={v} {sources.ref(id_docs.get(k))}" for k, v in sorted(ids.items())
                )
            )
        data.update({"name": c["display_name"], "country": c["country_code"], "identifiers": ids})

        since = date(date.today().year - strategy.years, 1, 1)
        for kind, metric, label in (
            ("financial", "revenue", "Umsatz"),
            ("employee", "headcount_total", "Mitarbeiter"),
        ):
            series = [
                r
                for r in repo.current_values(conn, company_id, kind, metric)
                if r["period_end"] >= since
            ]
            if not series:
                gap = (
                    conn.execute(
                        select(db.data_gap)
                        .where(
                            db.data_gap.c.company_id == company_id, db.data_gap.c.metric == metric
                        )
                        .order_by(db.data_gap.c.id.desc())
                    )
                    .mappings()
                    .first()
                )
                lines.append(
                    f"- {label}: **unbekannt**"
                    + (
                        f" (gesucht in: {gap['searched_sources']}; Grund: {gap['reason']})"
                        if gap
                        else ""
                    )
                )
                continue
            lines.append(f"- {label} (letzte {strategy.years} Jahre, gültige Werte):")
            for row in series:
                is_calc = row["provenance_class"] == "derived"
                tag = (
                    f"[Berechnung: {row['methodology']}] {sources.ref(row['source_document_id'])}"
                    if is_calc
                    else sources.ref(row["source_document_id"])
                )
                warn = " ⚠ Widerspruch zwischen Quellen" if row.get("conflict") else ""
                text = (
                    f"{label} {row['period_end'].year}: "
                    f"{fmt_number(row['value'], row['unit'] if kind == 'financial' else '')}"
                )
                unit_label = row["unit"] if kind == "financial" else "Personen"
                lines.append(
                    f"  - {row['period_end'].isoformat()}: "
                    f"{fmt_number(row['value'], unit_label)}"
                    f" ({row['provenance_class']}, {row['verification_status']}, "
                    f"Vertrauen {row['confidence']}) {tag}{warn}"
                    + (f" – {row['limitations']}" if row["limitations"] else "")
                )
                if not is_calc:
                    facts.append(Fact(f"F{len(facts) + 1}", text))
            by_currency: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
            for row in series:
                by_currency[row["currency"]].append(row)
            for currency, rows in by_currency.items():
                g = growth(rows)
                if g.cagr_pct is not None:
                    lines.append(
                        f"  - [Berechnung] CAGR {g.cagr_pct} % p. a. "
                        f"({g.first_period} → {g.last_period}, {g.points} Werte"
                        f"{', ' + currency if currency else ''}){'; ' + g.note if g.note else ''}"
                    )
                    if g.yoy:
                        lines.append(
                            "  - [Berechnung] Veränderung zum Vorjahr: "
                            + ", ".join(f"{d[:4]}: {v:+} %" for d, v in g.yoy)
                        )
        locs = (
            conn.execute(
                select(db.company_location).where(db.company_location.c.company_id == company_id)
            )
            .mappings()
            .all()
        )
        if locs:
            lines.append(
                "- Standorte: "
                + "; ".join(
                    f"{loc['location_type']} {loc['city'] or ''} {loc['country_code'] or ''} "
                    f"{sources.ref(loc['source_document_id'])}".replace("  ", " ")
                    for loc in locs[:8]
                )
            )
        parents = (
            conn.execute(
                select(db.parent_company_relationship, db.company.c.display_name)
                .join(
                    db.company,
                    db.company.c.id == db.parent_company_relationship.c.parent_company_id,
                )
                .where(db.parent_company_relationship.c.child_company_id == company_id)
            )
            .mappings()
            .all()
        )
        for p in parents:
            lines.append(
                f"- {p['relationship_type']}: {p['display_name']} "
                f"({p['basis']}) {sources.ref(p['source_document_id'])}"
            )
        hiring = (
            conn.execute(
                select(db.hiring_observation)
                .where(db.hiring_observation.c.company_id == company_id)
                .order_by(db.hiring_observation.c.observation_date.desc())
            )
            .mappings()
            .first()
        )
        if hiring:
            lines.append(
                f"- Offene Stellen am {hiring['observation_date']}: "
                f"{hiring['open_positions_count']} (strukturierte Stellenanzeigen der "
                f"Karriereseite) {sources.ref(hiring['source_document_id'])}"
            )
            facts.append(
                Fact(f"F{len(facts) + 1}", f"{hiring['open_positions_count']} offene Stellen")
            )
        signals = repo.signals_for(conn, company_id)[:6]
        for s in signals:
            lines.append(
                f"- Signal {s['signal_type']} ({s['event_date'] or 'Datum unbekannt'}): "
                f"„{s['quote'][:160]}“ {sources.ref(s['source_document_id'])}"
            )
        fits = (
            conn.execute(
                select(db.company_product_fit).where(
                    db.company_product_fit.c.company_id == company_id
                )
            )
            .mappings()
            .all()
        )
        best: dict[str, dict[str, Any]] = {}
        order = {"strong": 3, "plausible": 2, "weak": 1}
        for f in fits:
            if order.get(f["fit_level"], 0) > order.get(
                best.get(f["use_case_key"], {}).get("fit_level", ""), 0
            ):
                best[f["use_case_key"]] = dict(f)
        for key, fit in sorted(best.items()):
            evidence_ids = json.loads(fit["evidence_ids"])
            ev = (
                conn.execute(select(db.evidence).where(db.evidence.c.id.in_(evidence_ids)))
                .mappings()
                .first()
                if evidence_ids
                else None
            )
            quote = (
                f" – „{ev['quote'][:140]}“ {sources.ref(ev['source_document_id'])}" if ev else ""
            )
            lines.append(f"- Bezug {key}: **{fit['fit_level']}** ({fit['rationale'][:160]}){quote}")
            facts.append(Fact(f"F{len(facts) + 1}", f"Bezug {key}: {fit['fit_level']}"))
        data["fits"] = {k: v["fit_level"] for k, v in best.items()}
    if ai.available() and facts:
        summary = ai.summarize(facts)
        if summary:
            lines.append(
                f"\n> KI-Zusammenfassung (nur aus den Fakten oben, automatisch geprüft): {summary}"
            )
        for need in ai.derive_needs(facts, {k: c.name for k, c in cases.items()}):
            lines.append(
                f"- KI-Hypothese Bedarf {need['use_case']}: {need['rationale']} "
                f"({', '.join(need['fact_ids'])}; nicht belegt)"
            )
    lines.append("")
    return lines, data


def _ranking(sections: list[dict[str, Any]], engine: Engine) -> list[str]:
    ranked: list[tuple[Decimal, str, str]] = []
    unknown: list[str] = []
    with engine.connect() as conn:
        for item in sections:
            revenue = [
                r
                for r in repo.current_values(conn, item["company_id"], "financial", "revenue")
                if r["currency"] == "EUR"
            ]
            heads = repo.current_values(conn, item["company_id"], "employee", "headcount_total")
            if revenue:
                r = revenue[-1]
                ranked.append(
                    (
                        Decimal(r["value"]),
                        item["name"],
                        f"Umsatz {fmt_number(r['value'], 'EUR')} ({r['period_end'].year}"
                        f"{', berechnet' if r['provenance_class'] == 'derived' else ''})",
                    )
                )
            elif heads:
                h = heads[-1]
                unknown.append(
                    f"{item['name']} – Umsatz unbekannt, Mitarbeiter "
                    f"{fmt_number(h['value'])} ({h['period_end']})"
                )
            else:
                unknown.append(f"{item['name']} – Umsatz und Mitarbeiter unbekannt")
    lines = ["| Rang | Unternehmen | Größe (letzter gültiger Wert) |", "|---|---|---|"]
    for index, (_, name, text) in enumerate(sorted(ranked, key=lambda x: -x[0]), 1):
        lines.append(f"| {index} | {name} | {text} |")
    if unknown:
        lines += ["", "Nicht nach Umsatz einordenbar (keine geschätzten Werte):"]
        lines += [f"- {u}" for u in unknown]
    return lines


def build_report(
    engine: Engine, run_id: int, out_dir: Path, ai: Assistant, cases: dict[str, UseCase]
) -> list[Path]:
    """Bericht als Markdown, HTML und JSON schreiben."""
    run = _run(engine, run_id)
    params = json.loads(run["parameters"])
    strategy = SearchStrategy.from_dict(params["strategy"])
    sources = Sources(engine)
    with engine.connect() as conn:
        candidates = (
            conn.execute(select(db.run_candidate).where(db.run_candidate.c.run_id == run_id))
            .mappings()
            .all()
        )
        issues = (
            conn.execute(
                select(db.validation_issue).where(db.validation_issue.c.research_run_id == run_id)
            )
            .mappings()
            .all()
        )
        matches = (
            conn.execute(select(db.match_candidate).where(db.match_candidate.c.status == "open"))
            .mappings()
            .all()
        )
        failed = (
            conn.execute(
                select(db.research_task).where(
                    db.research_task.c.run_id == run_id, db.research_task.c.status == "failed"
                )
            )
            .mappings()
            .all()
        )
        opportunities = (
            conn.execute(
                select(db.opportunity_assessment, db.company.c.display_name)
                .join(db.company, db.company.c.id == db.opportunity_assessment.c.company_id)
                .where(db.opportunity_assessment.c.research_run_id == run_id)
                .order_by(db.opportunity_assessment.c.total_score.desc())
            )
            .mappings()
            .all()
        )
        changes = (
            conn.execute(
                select(db.change_event, db.company.c.display_name)
                .join(db.company, db.company.c.id == db.change_event.c.company_id)
                .where(db.change_event.c.research_run_id == run_id)
            )
            .mappings()
            .all()
        )
    selected = list(dict.fromkeys(c["company_id"] for c in candidates if c["selected"]))
    summary = json.loads(run["summary"] or "{}")

    lines = [
        f"# Recherchebericht – Auftrag {run_id}",
        "",
        f"Erstellt {db.utcnow():%Y-%m-%d %H:%M} UTC · Auftragsart: {run['run_type']}",
        "",
        "## 1. Auftrag und Suchstrategie",
        "",
        f"Auftrag: {strategy.task or '(nur Filter)'}",
        "",
        f"- Absichten: {', '.join(strategy.intents)}",
        f"- Länder: {', '.join(strategy.countries)}",
        f"- Genannte Unternehmen: {', '.join(strategy.companies) or 'keine'}",
        "- Namens-Suchbegriffe: "
        + (", ".join(f"{t} ({strategy.origin.get(t)})" for t in strategy.name_terms) or "keine"),
        f"- Branchenbegriffe: {', '.join(strategy.industry_terms) or 'keine'}",
        f"- Anwendungsfälle: {', '.join(strategy.use_cases) or 'keine'}",
        f"- Kennzahlen: {', '.join(strategy.metrics)}; Zeitraum: letzte {strategy.years} Jahre",
        "",
        "## 2. Ablauf",
        "",
        "| Schritt | ok | fehlgeschlagen | übersprungen | offen |",
        "|---|---|---|---|---|",
    ]
    for row in step_overview(engine, run_id):
        lines.append(
            f"| {row['step']}. {row['name']} | {row.get('succeeded', 0)} | "
            f"{row.get('failed', 0)} | {row.get('skipped', 0)} | "
            f"{row.get('pending', 0) + row.get('running', 0)} |"
        )
    lines += [
        "",
        f"Netzabrufe: {summary.get('requests', 0)}; Status: "
        f"{summary.get('fetch_status', {})}; KI: {summary.get('ai', 'none')}",
        "",
        f"Kandidaten gefunden: {len({c['company_id'] for c in candidates})}, "
        f"untersucht: {len(selected)}",
        "",
    ]

    sections: list[dict[str, Any]] = []
    body: list[str] = ["## 4. Unternehmen (belegte Fakten)", ""]
    for company_id in selected:
        section_lines, data = _company_section(
            engine, company_id, run_id, strategy, sources, ai, cases
        )
        body += section_lines
        sections.append(data)

    lines += ["## 3. Ergebnis", ""]
    if set(strategy.intents) & {
        "market_leaders",
        "competitors",
        "comparison",
        "company_development",
    }:
        lines += [
            "Rangfolge nach belegtem Umsatz (EUR; Fremdwährungen als gekennzeichnete Berechnung):",
            "",
            *_ranking(sections, engine),
            "",
        ]
    if opportunities:
        lines += [
            "Potenzielle Kunden (Bewertung zerlegbar, siehe Export „chancen“):",
            "",
            "| Unternehmen | Anwendungsfall | Priorität | Score | Datenvollständigkeit |",
            "|---|---|---|---|---|",
        ]
        seen: set[tuple[int, str]] = set()
        for o in opportunities:
            key = (o["company_id"], o["use_case_key"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"| {o['display_name']} | {o['use_case_key']} | {o['priority']} | "
                f"{o['total_score']} | {o['data_completeness_pct']} % |"
            )
        lines.append("")
    if changes:
        lines += ["Veränderungen gegenüber dem bisherigen Stand:", ""]
        for ch in changes[:30]:
            lines.append(
                f"- {ch['display_name']}: {ch['metric']} {ch['period_end']} "
                f"{ch['change_kind']} {fmt_number(ch['old_value'])} → "
                f"{fmt_number(ch['new_value'])} {ch['unit'] or ''}"
            )
        lines.append("")
    lines += body

    lines += ["## 5. Unsicherheiten", ""]
    warns = [i for i in issues if i["severity"] in ("warning", "error", "hint")]
    for i in warns[:60]:
        lines.append(
            f"- {i['severity']} {i['code']}: {i['message']}"
            + (f" {sources.ref(i['source_document_id'])}" if i["source_document_id"] else "")
        )
    if not warns:
        lines.append("- keine Prüfhinweise")
    lines += ["", "## 6. Offene Fragen", ""]
    for m in matches[:30]:
        lines.append(
            f"- Abgleich prüfen ({m['kind']}): Firma {m['company_a_id']} ↔ "
            f"{m['company_b_id']} – {m['reasons']}"
        )
    for t in failed[:30]:
        lines.append(f"- Fehlgeschlagen: {t['label'] or t['task_type']} – {t['error']}")
    for note in summary.get("notes", [])[:40]:
        if any(
            w in note
            for w in ("Kandidat", "nicht zusammengeführt", "nicht übernommen", "keine Firma")
        ):
            lines.append(f"- {note}")
    lines += ["", "## 7. Quellenverzeichnis", ""] + (sources.listing() or ["- keine"])
    lines += [
        "",
        "Hinweis: Webseiten können sich nach dem Abruf ändern; maßgeblich ist der "
        "gespeicherte Stand (SHA-256) zum Abrufzeitpunkt.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "bericht.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    htm = out_dir / "bericht_auftrag.html"
    htm.write_text(
        markdown_to_html("\n".join(lines), f"Recherchebericht {run_id}"), encoding="utf-8"
    )
    js = out_dir / "bericht.json"
    js.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "strategy": strategy.to_dict(),
                "steps": step_overview(engine, run_id),
                "companies": sections,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return [md, htm, js]


def markdown_to_html(text: str, title: str) -> str:
    """Minimaler, sicherer Markdown-Konverter (Überschriften, Listen, Tabellen, Absätze)."""
    out = [
        "<!doctype html><html lang=de><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>{html.escape(title)}</title><style>body{{font:14px system-ui,sans-serif;"
        "margin:16px;max-width:1100px;color:#1d1d1f;background:#fff}table{border-collapse:"
        "collapse}td,th{border:1px solid #ccc;padding:3px 6px}"
        "@media (prefers-color-scheme:dark){body{background:#111;color:#eee}}</style>"
        "</head><body>",
    ]
    in_list = in_table = False
    for raw in text.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if raw.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r"-+", c) for c in cells):
                continue
            if not in_table:
                out.append("<div style='overflow-x:auto'><table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table></div>")
            in_table = False
        stripped = raw.lstrip()
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line.lstrip()[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if raw.startswith("#"):
            level = len(raw) - len(raw.lstrip("#"))
            out.append(f"<h{level}>{line[level:].strip()}</h{level}>")
        elif raw.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table></div>")
    out.append("</body></html>")
    return "".join(out)
