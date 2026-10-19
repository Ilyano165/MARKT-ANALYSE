"""Research Engine: Rechercheaufträge in acht Schritten, pausierbar und fortsetzbar.

1 Suchstrategie · 2 Kandidaten finden · 3 Quellen untersuchen · 4 Informationen extrahieren ·
5 Unternehmen abgleichen · 6 Angaben validieren · 7 Ergebnisse speichern · 8 Bericht erzeugen

Jeder Schritt besteht aus Aufgaben in ``research_task`` (Status pending/running/succeeded/
failed/skipped). Folgeaufgaben werden zur Laufzeit angelegt. Ein fehlgeschlagener Adapter
scheitert nur als einzelne Aufgabe. Strg+C pausiert; ``resume`` setzt fort. Budgets
(Abrufe, Aufgaben, Zeit, KI-Aufrufe, Kosten) werden vor jeder Aufgabe geprüft.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, insert, select, update

from . import db, repo
from .context import Ctx
from .engine import adapters as adapter_registry
from .engine.budget import Budget
from .engine.changes import record_changes
from .engine.derive import SEARCH_PROFILE_KEY, convert_revenue_to_eur, record_gaps
from .engine.extract import extract_company_documents, issue, summarize_report
from .engine.matching import find_match_candidates
from .engine.report import STEP_NAMES, build_report, step_overview
from .engine.strategy import ResearchRequest, SearchStrategy, build_strategy
from .engine.validation import revenue_per_employee_issue
from .export import export_all
from .llm.assistant import Fact
from .scoring import score_use_case
from .usecases import UseCase

TaskResult = tuple[str, dict[str, Any]]  # (Status, Ergebnis)
Handler = Callable[[Ctx, dict[str, Any], dict[str, UseCase]], TaskResult]
METRIC_KINDS = {"revenue": "financial", "headcount_total": "employee"}


# ---------------------------------------------------------------------- Auftrag anlegen


def create_research(
    engine: Engine,
    request: ResearchRequest,
    budget: Budget,
    out_dir: Path,
    run_type: str = "research",
) -> int:
    """Auftrag mit erster Aufgabe (Schritt 1) anlegen."""
    with engine.begin() as conn:
        run_id = int(
            conn.execute(
                insert(db.research_run).values(
                    run_type=run_type,
                    status="queued",
                    created_at=db.utcnow(),
                    parameters=json.dumps(
                        {
                            "request": request.to_dict(),
                            "budget": budget.to_dict(),
                            "out_dir": str(out_dir),
                        },
                        ensure_ascii=False,
                    ),
                    summary="{}",
                )
            ).inserted_primary_key[0]
        )
    enqueue(engine, run_id, 1, "plan", {}, "Suchstrategie erstellen")
    return run_id


def enqueue(
    engine: Engine, run_id: int, step: int, task_type: str, params: dict[str, Any], label: str
) -> bool:
    """Aufgabe anhängen (gleiche Aufgabe im selben Auftrag nur einmal)."""
    key = hashlib.sha256(f"{task_type}|{json.dumps(params, sort_keys=True)}".encode()).hexdigest()
    with engine.begin() as conn:
        if conn.execute(
            select(db.research_task.c.id).where(
                db.research_task.c.run_id == run_id, db.research_task.c.idempotency_key == key
            )
        ).first():
            return False
        seq = (
            conn.execute(
                select(func.max(db.research_task.c.seq)).where(db.research_task.c.run_id == run_id)
            ).scalar()
            or 0
        ) + 1
        conn.execute(
            insert(db.research_task).values(
                run_id=run_id,
                seq=seq,
                step=step,
                task_type=task_type,
                label=label[:300],
                params=json.dumps(params, ensure_ascii=False),
                idempotency_key=key,
                status="pending",
                attempts=0,
            )
        )
    return True


def run_parameters(engine: Engine, run_id: int) -> dict[str, Any]:
    with engine.connect() as conn:
        raw = conn.execute(
            select(db.research_run.c.parameters).where(db.research_run.c.id == run_id)
        ).scalar()
    return dict(json.loads(raw or "{}"))


def _save_parameters(engine: Engine, run_id: int, params: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(db.research_run)
            .where(db.research_run.c.id == run_id)
            .values(parameters=json.dumps(params, ensure_ascii=False, default=str))
        )


def _strategy(ctx: Ctx) -> SearchStrategy:
    return SearchStrategy.from_dict(run_parameters(ctx.engine, ctx.run_id or 0)["strategy"])


def _selected(ctx: Ctx) -> list[int]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(db.run_candidate.c.company_id)
            .where(db.run_candidate.c.run_id == ctx.run_id, db.run_candidate.c.selected.is_(True))
            .order_by(db.run_candidate.c.id)
        ).all()
    return list(dict.fromkeys(r.company_id for r in rows))


# ---------------------------------------------------------------------- Schritt 1


def _plan(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    run_params = run_parameters(ctx.engine, ctx.run_id or 0)
    request = ResearchRequest(**run_params["request"])
    strategy = build_strategy(request, cases, ctx.ai)
    strategy.max_candidates = min(strategy.max_candidates, ctx.budget.max_candidates)
    run_params["strategy"] = strategy.to_dict()
    _save_parameters(ctx.engine, ctx.run_id or 0, run_params)
    _register_search_profile(ctx.engine, strategy)
    run_id = ctx.run_id or 0
    for name in strategy.companies:
        enqueue(
            ctx.engine,
            run_id,
            2,
            "resolve_named",
            {"query": name},
            f"Genanntes Unternehmen „{name}“ auflösen",
        )
    only_named = bool(strategy.companies) and set(strategy.intents) <= {
        "comparison",
        "company_development",
    }
    if not only_named:
        for adapter in adapter_registry.ADAPTERS:
            if adapter.finds and adapter.enabled(ctx):
                enqueue(
                    ctx.engine,
                    run_id,
                    2,
                    "find",
                    {"adapter": adapter.key},
                    f"Kandidaten suchen: {adapter.key}",
                )
    enqueue(ctx.engine, run_id, 2, "select", {}, "Kandidaten auswählen")
    return "succeeded", {"strategy": strategy.to_dict()}


def _register_search_profile(engine: Engine, strategy: SearchStrategy) -> None:
    with engine.begin() as conn:
        values = {
            "name": "Suchprofil des Auftrags (Begriffe der Recherche)",
            "product_categories": "[]",
            "evidence_status": "search_profile",
            "definition": json.dumps({"web_terms": strategy.web_terms}, ensure_ascii=False),
        }
        if conn.execute(
            select(db.product_use_case.c.key).where(db.product_use_case.c.key == SEARCH_PROFILE_KEY)
        ).first():
            conn.execute(
                update(db.product_use_case)
                .where(db.product_use_case.c.key == SEARCH_PROFILE_KEY)
                .values(**values)
            )
        else:
            conn.execute(insert(db.product_use_case).values(key=SEARCH_PROFILE_KEY, **values))


# ---------------------------------------------------------------------- Schritt 2


def _resolve_named(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    strategy = _strategy(ctx)
    company_id, notes = adapter_registry.resolve_named(
        ctx, params["query"], strategy.countries if len(strategy.countries) == 1 else []
    )
    for note in notes:
        ctx.note(note)
    if company_id is None:
        return "failed", {"query": params["query"], "notes": notes}
    adapter_registry.register_candidate(
        ctx,
        ctx.run_id or 0,
        adapter_registry.Candidate(
            company_id, f"im Auftrag genannt: {params['query']}", None, "named"
        ),
        "auftrag",
    )
    return "succeeded", {"company_id": company_id, "notes": notes}


def _find(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    adapter = adapter_registry.ADAPTER_BY_KEY[params["adapter"]]
    strategy = _strategy(ctx)
    found = adapter.find_candidates(ctx, strategy, cases)
    for candidate in found:
        adapter_registry.register_candidate(ctx, ctx.run_id or 0, candidate, adapter.key)
    return "succeeded", {"candidates": len(found)}


def _select(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    strategy = _strategy(ctx)
    run_id = ctx.run_id or 0
    with ctx.engine.connect() as conn:
        rows = (
            conn.execute(select(db.run_candidate).where(db.run_candidate.c.run_id == run_id))
            .mappings()
            .all()
        )
        fit_rank = {"strong": 3, "plausible": 2, "weak": 1}
        best_fit: dict[int, int] = {}
        for f in conn.execute(
            select(db.company_product_fit.c.company_id, db.company_product_fit.c.fit_level)
        ):
            best_fit[f.company_id] = max(
                best_fit.get(f.company_id, 0), fit_rank.get(f.fit_level, 0)
            )
        info = {
            r.company_id: (
                bool(
                    conn.execute(
                        select(db.company.c.website_domain).where(db.company.c.id == r.company_id)
                    ).scalar()
                ),
                r.role,
            )
            for r in rows
        }
    named = [r["company_id"] for r in rows if r["role"] == "named"]
    sources: dict[int, set[str]] = {}
    for r in rows:
        sources.setdefault(r["company_id"], set()).add(r["adapter_key"])
    others = sorted(
        (cid for cid in sources if cid not in named),
        key=lambda cid: (-len(sources[cid]), -best_fit.get(cid, 0), not info[cid][0], cid),
    )
    chosen = list(dict.fromkeys(named + others))[: max(strategy.max_candidates, len(named))]
    with ctx.engine.begin() as conn:
        conn.execute(
            update(db.run_candidate)
            .where(db.run_candidate.c.run_id == run_id, db.run_candidate.c.company_id.in_(chosen))
            .values(selected=True)
        )
    investigators = [a for a in adapter_registry.ADAPTERS if a.investigates and a.enabled(ctx)]
    for company_id in chosen:
        for adapter in investigators:
            enqueue(
                ctx.engine,
                run_id,
                3,
                "investigate",
                {"adapter": adapter.key, "company_id": company_id},
                f"{adapter.key} → Firma {company_id}",
            )
    for company_id in chosen:
        enqueue(
            ctx.engine,
            run_id,
            4,
            "extract",
            {"company_id": company_id},
            f"Extraktion Firma {company_id}",
        )
    enqueue(ctx.engine, run_id, 5, "match", {}, "Unternehmen abgleichen")
    enqueue(ctx.engine, run_id, 6, "validate", {}, "Angaben validieren")
    enqueue(ctx.engine, run_id, 7, "store", {}, "Ableiten, bewerten, Veränderungen, Export")
    enqueue(ctx.engine, run_id, 8, "report", {}, "Bericht erzeugen")
    return "succeeded", {
        "found": len(sources),
        "selected": chosen,
        "not_selected": len(sources) - len(chosen),
    }


# ---------------------------------------------------------------------- Schritte 3 und 4


def _investigate(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    adapter = adapter_registry.ADAPTER_BY_KEY[params["adapter"]]
    company_id = int(params["company_id"])
    strategy = _strategy(ctx)
    if not strategy.refresh:
        cutoff = db.utcnow() - timedelta(days=ctx.settings.refresh_days)
        with ctx.engine.connect() as conn:
            recent = (
                conn.execute(
                    select(db.investigation_log)
                    .where(
                        db.investigation_log.c.company_id == company_id,
                        db.investigation_log.c.adapter_key == adapter.key,
                        db.investigation_log.c.status == "ok",
                        db.investigation_log.c.finished_at >= cutoff,
                        db.investigation_log.c.run_id != ctx.run_id,
                    )
                    .order_by(db.investigation_log.c.id.desc())
                )
                .mappings()
                .first()
            )
        if recent is not None:
            documents = (
                _previous_documents(ctx, company_id, adapter.key)
                if adapter.key == "website"
                else []
            )
            return "skipped", {
                "reason": f"bereits untersucht am {recent['finished_at']:%Y-%m-%d}"
                " (refresh für Neuabruf)",
                "documents": documents,
            }
    outcome = adapter.investigate(ctx, company_id, strategy, cases)
    with ctx.engine.begin() as conn:
        conn.execute(
            insert(db.investigation_log).values(
                company_id=company_id,
                adapter_key=adapter.key,
                run_id=ctx.run_id,
                status=outcome.status,
                detail=outcome.detail[:1000],
                finished_at=db.utcnow(),
            )
        )
    status = {"ok": "succeeded", "failed": "failed"}.get(outcome.status, "skipped")
    return status, {
        "detail": outcome.detail,
        "found": outcome.found,
        "documents": outcome.documents,
    }


def _previous_documents(ctx: Ctx, company_id: int, adapter_key: str) -> list[int]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(db.research_task.c.result, db.research_task.c.params).where(
                db.research_task.c.task_type == "investigate",
                db.research_task.c.status == "succeeded",
            )
        ).all()
    for row in reversed(rows):
        params = json.loads(row.params)
        if params.get("company_id") == company_id and params.get("adapter") == adapter_key:
            return list(json.loads(row.result or "{}").get("documents", []))
    return []


def _extract(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    company_id = int(params["company_id"])
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(db.research_task.c.params, db.research_task.c.result).where(
                db.research_task.c.run_id == ctx.run_id,
                db.research_task.c.task_type == "investigate",
                db.research_task.c.status.in_(["succeeded", "skipped"]),
            )
        ).all()
    documents: list[int] = []
    for row in rows:
        p = json.loads(row.params)
        if p.get("company_id") == company_id and p.get("adapter") == "website":
            documents += json.loads(row.result or "{}").get("documents", [])
    if not documents:
        return "skipped", {"reason": "keine Dokumente zur Auswertung"}
    strategy = _strategy(ctx)
    use_cases = {k: cases[k] for k in strategy.use_cases if k in cases}
    report = extract_company_documents(ctx, company_id, documents, use_cases, strategy.web_terms)
    return "succeeded", summarize_report(report)


# ---------------------------------------------------------------------- Schritte 5–8


def _match(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    with ctx.engine.connect() as conn:
        found = conn.execute(
            select(db.run_candidate.c.company_id).where(db.run_candidate.c.run_id == ctx.run_id)
        ).all()
    ids = sorted({r.company_id for r in found})
    with ctx.engine.begin() as conn:
        created = find_match_candidates(conn, ids, ctx.run_id)
    return "succeeded", {"match_candidates": created, "companies": len(ids)}


def _validate(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    strategy = _strategy(ctx)
    selected = _selected(ctx)
    wanted = tuple((METRIC_KINDS[m], m) for m in strategy.metrics if m in METRIC_KINDS)
    counts = {"conflicts": 0, "ratio_warnings": 0, "gaps": 0}
    for company_id in selected:
        with ctx.engine.connect() as conn:
            revenue = {
                r["period_end"].year: r
                for r in repo.current_values(conn, company_id, "financial", "revenue")
                if r["currency"] == "EUR"
            }
            heads = {
                r["period_end"].year: r
                for r in repo.current_values(conn, company_id, "employee", "headcount_total")
            }
            conflicts = [
                r
                for kind in ("financial", "employee")
                for r in repo.current_values(
                    conn, company_id, kind, "revenue" if kind == "financial" else "headcount_total"
                )
                if r["conflict"]
            ]
        for row in conflicts:
            counts["conflicts"] += 1
            issue(
                ctx,
                company_id,
                "warning",
                "conflicting_sources",
                f"{row['metric']} {row['period_end']}: {row['alternatives'] + 1} Angaben "
                f"weichen voneinander ab; angezeigt wird {row['value']} "
                f"({row['provenance_class']})",
                row["source_document_id"],
            )
        for year in sorted(set(revenue) & set(heads)):
            problem = revenue_per_employee_issue(
                Decimal(revenue[year]["value"]), Decimal(heads[year]["value"])
            )
            if problem:
                counts["ratio_warnings"] += 1
                issue(ctx, company_id, "warning", "revenue_per_employee", f"{year}: {problem}")
        searched = sorted(
            {a.key for a in adapter_registry.ADAPTERS if a.investigates and a.enabled(ctx)}
        )
        counts["gaps"] += len(record_gaps(ctx, company_id, searched, wanted or ()))
        if ctx.ai.available():
            facts = _facts_for(ctx, company_id)
            for hint in ctx.ai.flag_contradictions(facts):
                issue(
                    ctx,
                    company_id,
                    "hint",
                    "llm_contradiction_hint",
                    f"KI-Hinweis (ungeprüft): {hint['reason']} – {', '.join(hint['fact_ids'])}",
                )
    return "succeeded", counts


def _facts_for(ctx: Ctx, company_id: int) -> list[Fact]:
    facts: list[Fact] = []
    with ctx.engine.connect() as conn:
        for kind, metric in (("financial", "revenue"), ("employee", "headcount_total")):
            for row in repo.current_values(conn, company_id, kind, metric):
                facts.append(
                    Fact(
                        f"F{len(facts) + 1}",
                        f"{metric} {row['period_end']}: {row['value']} {row['unit']}",
                    )
                )
        for loc in conn.execute(
            select(db.company_location).where(db.company_location.c.company_id == company_id)
        ).mappings():
            facts.append(
                Fact(
                    f"F{len(facts) + 1}",
                    f"{loc['location_type']}: {loc['city']} {loc['country_code']}",
                )
            )
    return facts


def _store(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    strategy = _strategy(ctx)
    selected = _selected(ctx)
    converted = sum(convert_revenue_to_eur(ctx, cid) for cid in selected)
    scored = {u: score_use_case(ctx.engine, u, ctx.run_id) for u in strategy.use_cases}
    with ctx.engine.begin() as conn:
        changes = record_changes(conn, ctx.run_id or 0)
    out_dir = Path(run_parameters(ctx.engine, ctx.run_id or 0)["out_dir"])
    files = export_all(
        ctx.engine, out_dir / "export", selected, selected, strategy.use_cases, ctx.run_id
    )
    return "succeeded", {
        "fx_conversions": converted,
        "scored": scored,
        "changes": changes,
        "files": [str(f) for f in files],
    }


def _report(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> TaskResult:
    _write_summary(ctx, "running")
    out_dir = Path(run_parameters(ctx.engine, ctx.run_id or 0)["out_dir"])
    files = build_report(ctx.engine, ctx.run_id or 0, out_dir, ctx.ai, cases)
    return "succeeded", {"files": [str(f) for f in files]}


HANDLERS: dict[str, Handler] = {
    "plan": _plan,
    "resolve_named": _resolve_named,
    "find": _find,
    "select": _select,
    "investigate": _investigate,
    "extract": _extract,
    "match": _match,
    "validate": _validate,
    "store": _store,
    "report": _report,
}


# ---------------------------------------------------------------------- Ausführung


def _next_task(engine: Engine, run_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(db.research_task)
                .where(
                    db.research_task.c.run_id == run_id,
                    db.research_task.c.status.in_(["pending", "running"]),
                )
                .order_by(db.research_task.c.seq)
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def _summary(engine: Engine, run_id: int) -> dict[str, Any]:
    with engine.connect() as conn:
        raw = conn.execute(
            select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
        ).scalar()
    return dict(json.loads(raw or "{}"))


def _budget_problem(ctx: Ctx, run_id: int, started: float, previous_seconds: float) -> str:
    with ctx.engine.connect() as conn:
        done = (
            conn.execute(
                select(func.count())
                .select_from(db.research_task)
                .where(
                    db.research_task.c.run_id == run_id,
                    db.research_task.c.status.notin_(["pending", "running"]),
                )
            ).scalar()
            or 0
        )
    if done >= ctx.budget.max_tasks:
        return f"Aufgabenlimit {ctx.budget.max_tasks} erreicht"
    if previous_seconds + time.monotonic() - started >= ctx.budget.max_seconds:
        return f"Zeitlimit {ctx.budget.max_seconds} s erreicht"
    if ctx.gateway.stats.requests >= ctx.gateway.max_requests:
        return f"Abruflimit {ctx.budget.max_requests} erreicht"
    if ctx.ai.provider.calls >= ctx.budget.max_llm_calls and ctx.ai.available():
        return f"KI-Aufruflimit {ctx.budget.max_llm_calls} erreicht"
    return ""


def execute(
    ctx: Ctx, run_id: int, cases: dict[str, UseCase], progress: Callable[[str], None] = print
) -> str:
    """Offene Aufgaben ausführen. Endstatus: completed, completed_with_gaps, paused,
    budget_exhausted."""
    ctx.run_id = run_id
    ctx.gateway.run_id = run_id
    summary = _summary(ctx.engine, run_id)
    previous_seconds = float(summary.get("seconds_total", 0.0))
    ctx.gateway.requests_offset = int(summary.get("requests_total", 0))
    started = time.monotonic()
    _set_run(ctx.engine, run_id, status="running", started_at=db.utcnow())
    try:
        while True:
            problem = _budget_problem(ctx, run_id, started, previous_seconds)
            if problem:
                ctx.note(f"Budget: {problem} – Auftrag mit höherem Budget fortsetzbar")
                return _finish(ctx, run_id, "budget_exhausted", started, previous_seconds)
            task = _next_task(ctx.engine, run_id)
            if task is None:
                break
            params = json.loads(task["params"])
            _set_task(
                ctx.engine,
                task["id"],
                status="running",
                started_at=db.utcnow(),
                attempts=task["attempts"] + 1,
            )
            before = dict(ctx.gateway.stats.by_status)
            try:
                status, result = HANDLERS[task["task_type"]](ctx, params, cases)
                status, result = _check_fetches(status, result, before, ctx.gateway.stats.by_status)
            except KeyboardInterrupt:
                _set_task(ctx.engine, task["id"], status="pending")
                raise
            except Exception as exc:  # Adapter/Format unerwartet: nur diese Aufgabe scheitert
                status, result = "failed", {}
                _set_task(ctx.engine, task["id"], error=f"{type(exc).__name__}: {exc}"[:2000])
                ctx.note(f"{task['label'] or task['task_type']} gescheitert: {exc}")
            _set_task(
                ctx.engine,
                task["id"],
                status=status,
                finished_at=db.utcnow(),
                result=json.dumps(result, ensure_ascii=False, default=str),
                **(
                    {
                        "error": str(
                            result.get("detail")
                            or result.get("reason")
                            or "; ".join(result.get("notes", []))
                        )[:2000]
                    }
                    if status == "failed" and result
                    else {}
                ),
            )
            progress(_progress_line(ctx.engine, run_id, task, status, result))
    except KeyboardInterrupt:
        return _finish(ctx, run_id, "paused", started, previous_seconds)
    with ctx.engine.connect() as conn:
        failed = conn.execute(
            select(func.count())
            .select_from(db.research_task)
            .where(db.research_task.c.run_id == run_id, db.research_task.c.status == "failed")
        ).scalar()
    status = "completed_with_gaps" if failed or ctx.notes else "completed"
    return _finish(ctx, run_id, status, started, previous_seconds)


_SUCCESS = ("ok", "cached", "not_modified")


def _check_fetches(
    status: str, result: dict[str, Any], before: dict[str, int], after: dict[str, int]
) -> TaskResult:
    """Aufgabe mit Abrufen, von denen keiner gelang, gilt als fehlgeschlagen – nicht als ok."""
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    attempted = sum(v for v in delta.values() if v > 0)
    succeeded = sum(delta.get(k, 0) for k in _SUCCESS)
    result = {**result, "fetches": {k: v for k, v in delta.items() if v}}
    if status in ("succeeded", "failed") and attempted and not succeeded:
        failed = ", ".join(f"{k} {v}" for k, v in delta.items() if v)
        previous = f"; {result['detail']}" if result.get("detail") else ""
        return "failed", {
            **result,
            "detail": f"alle Quellenabrufe gescheitert ({failed}){previous}",
        }
    return status, result


def _progress_line(
    engine: Engine, run_id: int, task: dict[str, Any], status: str, result: dict[str, Any]
) -> str:
    step = int(task["step"])
    rows = {r["step"]: r for r in step_overview(engine, run_id)}
    r = rows.get(step, {})
    total = sum(v for k, v in r.items() if isinstance(v, int) and k != "step")
    done = r.get("succeeded", 0) + r.get("failed", 0) + r.get("skipped", 0)
    mark = {"succeeded": "ok", "failed": "FEHLER", "skipped": "übersprungen"}.get(status, status)
    detail = result.get("detail") or result.get("reason") or ""
    return (
        f"[Schritt {step}/8 {STEP_NAMES.get(step, '')}] {done}/{total} "
        f"(ok {r.get('succeeded', 0)}, Fehler {r.get('failed', 0)}, übersprungen "
        f"{r.get('skipped', 0)}) – {task['label']}: {mark}"
        + (f" – {str(detail)[:120]}" if detail else "")
    )


def _write_summary(ctx: Ctx, status: str, seconds_total: float | None = None) -> None:
    previous = _summary(ctx.engine, ctx.run_id or 0)
    summary = {
        "notes": (previous.get("notes", []) + ctx.notes)[-300:]
        if status != "running"
        else previous.get("notes", []) + ctx.notes,
        "requests": ctx.gateway.stats.requests + int(previous.get("requests_total", 0)),
        "requests_total": int(previous.get("requests_total", 0))
        + (ctx.gateway.stats.requests if status != "running" else 0),
        "fetch_status": ctx.gateway.stats.by_status,
        "fetch_by_source": ctx.gateway.stats.by_source,
        "ai": ctx.ai.name,
        "ai_calls": ctx.ai.provider.calls,
        "seconds_total": seconds_total
        if seconds_total is not None
        else previous.get("seconds_total", 0.0),
    }
    _set_run(ctx.engine, ctx.run_id or 0, summary=json.dumps(summary, ensure_ascii=False))


def _finish(ctx: Ctx, run_id: int, status: str, started: float, previous_seconds: float) -> str:
    _write_summary(ctx, status, previous_seconds + time.monotonic() - started)
    ctx.notes.clear()
    _set_run(ctx.engine, run_id, status=status, finished_at=db.utcnow())
    return status


def _set_run(engine: Engine, run_id: int, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(update(db.research_run).where(db.research_run.c.id == run_id).values(**values))


def _set_task(engine: Engine, task_id: int, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(db.research_task).where(db.research_task.c.id == task_id).values(**values)
        )


def task_results(engine: Engine, run_id: int) -> list[tuple[str, dict[str, Any]]]:
    """Ergebnisse erfolgreicher Aufgaben in Reihenfolge."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.research_task.c.task_type, db.research_task.c.result)
            .where(db.research_task.c.run_id == run_id, db.research_task.c.status == "succeeded")
            .order_by(db.research_task.c.seq)
        ).all()
    return [(r.task_type, json.loads(r.result or "{}")) for r in rows]


def named_companies(engine: Engine, run_id: int) -> list[int]:
    """Im Auftrag ausdrücklich genannte und aufgelöste Firmen."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.run_candidate.c.company_id)
            .where(db.run_candidate.c.run_id == run_id, db.run_candidate.c.role == "named")
            .order_by(db.run_candidate.c.id)
        ).all()
    return list(dict.fromkeys(r.company_id for r in rows))


def update_budget(engine: Engine, run_id: int, **limits: Any) -> Budget:
    """Budget eines Auftrags anheben (für ``resume``)."""
    params = run_parameters(engine, run_id)
    budget = Budget.from_dict(
        {**params.get("budget", {}), **{k: v for k, v in limits.items() if v is not None}}
    )
    params["budget"] = budget.to_dict()
    _save_parameters(engine, run_id, params)
    return budget
