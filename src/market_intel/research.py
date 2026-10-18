"""Rechercheaufträge (Runs) aus einzelnen Aufgaben: pausierbar, fortsetzbar, nachvollziehbar.

Jede Aufgabe wird vor und nach der Ausführung gespeichert. Ein Abbruch (Strg+C) setzt den
Run auf ``paused``; ``resume`` setzt bei der ersten nicht erledigten Aufgabe fort. Bereits
gespeicherte Daten werden dabei nicht doppelt angelegt (Duplikaterkennung im Repository).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, insert, select, update

from . import db
from .context import Ctx
from .discovery import discover
from .export import export_all
from .profile import profile_company
from .scoring import score_use_case
from .usecases import UseCase

Handler = Callable[[Ctx, dict[str, Any], dict[str, UseCase]], dict[str, Any]]


def create_run(
    engine: Engine,
    run_type: str,
    parameters: dict[str, Any],
    tasks: list[tuple[str, dict[str, Any]]],
) -> int:
    """Run mit Aufgabenliste anlegen (Status ``queued``)."""
    with engine.begin() as conn:
        run_id = int(
            conn.execute(
                insert(db.research_run).values(
                    run_type=run_type,
                    parameters=json.dumps(parameters, ensure_ascii=False),
                    status="queued",
                    created_at=db.utcnow(),
                )
            ).inserted_primary_key[0]
        )
        for seq, (task_type, params) in enumerate(tasks, start=1):
            key = hashlib.sha256(
                f"{task_type}|{json.dumps(params, sort_keys=True)}".encode()
            ).hexdigest()
            conn.execute(
                insert(db.research_task).values(
                    run_id=run_id,
                    seq=seq,
                    task_type=task_type,
                    params=json.dumps(params, ensure_ascii=False),
                    idempotency_key=key,
                    status="pending",
                )
            )
    return run_id


def _profile(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> dict[str, Any]:
    report = profile_company(ctx, params["query"], cases, params.get("country"))
    return {
        "company_id": report.company_id,
        "sources": report.sources_used,
        "candidates": report.candidates,
    }


def _discover(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> dict[str, Any]:
    case = cases[params["use_case"]]
    report = discover(
        ctx, case, params["country"], max_websites=int(params.get("max_websites", 15)), cases=cases
    )
    return {
        "companies": sorted(report.companies),
        "fits": dict(report.fits),
        "signals": report.signals,
        "websites_checked": report.websites_checked,
    }


def _score(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> dict[str, Any]:
    return {"scored": score_use_case(ctx.engine, params["use_case"], ctx.run_id)}


def _export(ctx: Ctx, params: dict[str, Any], cases: dict[str, UseCase]) -> dict[str, Any]:
    results = task_results(ctx.engine, ctx.run_id or 0)
    compare_ids = [r["company_id"] for t, r in results if t == "profile" and r.get("company_id")]
    company_ids = set(compare_ids)
    for task_type, result in results:
        if task_type == "discover":
            company_ids.update(result.get("companies", []))
    use_cases = [
        json.loads(p)["use_case"] for p in _task_params(ctx.engine, ctx.run_id or 0, "discover")
    ]
    files = export_all(
        ctx.engine, Path(params["out_dir"]), sorted(company_ids), compare_ids, use_cases, ctx.run_id
    )
    return {"files": [str(f) for f in files], "compared": compare_ids}


HANDLERS: dict[str, Handler] = {
    "profile": _profile,
    "discover": _discover,
    "score": _score,
    "export": _export,
}


def _task_params(engine: Engine, run_id: int, task_type: str) -> list[str]:
    with engine.connect() as conn:
        return [
            r.params
            for r in conn.execute(
                select(db.research_task.c.params).where(
                    db.research_task.c.run_id == run_id, db.research_task.c.task_type == task_type
                )
            )
        ]


def task_results(engine: Engine, run_id: int) -> list[tuple[str, dict[str, Any]]]:
    """Ergebnisse erledigter Aufgaben in Reihenfolge."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.research_task.c.task_type, db.research_task.c.result)
            .where(db.research_task.c.run_id == run_id, db.research_task.c.status == "succeeded")
            .order_by(db.research_task.c.seq)
        ).all()
    return [(r.task_type, json.loads(r.result or "{}")) for r in rows]


def execute(
    ctx: Ctx, run_id: int, cases: dict[str, UseCase], progress: Callable[[str], None] = print
) -> str:
    """Offene Aufgaben ausführen. Gibt den Endstatus zurück."""
    ctx.run_id = run_id
    ctx.gateway.run_id = run_id
    _set_run(ctx.engine, run_id, status="running", started_at=db.utcnow())
    with ctx.engine.connect() as conn:
        tasks = (
            conn.execute(
                select(db.research_task)
                .where(
                    db.research_task.c.run_id == run_id,
                    db.research_task.c.status.in_(["pending", "running", "failed"]),
                )
                .order_by(db.research_task.c.seq)
            )
            .mappings()
            .all()
        )
    failed = 0
    try:
        for task in tasks:
            params = json.loads(task["params"])
            progress(f"[{task['seq']}] {task['task_type']} {params}")
            _set_task(
                ctx.engine,
                task["id"],
                status="running",
                started_at=db.utcnow(),
                attempts=task["attempts"] + 1,
            )
            try:
                result = HANDLERS[task["task_type"]](ctx, params, cases)
            except KeyboardInterrupt:
                _set_task(ctx.engine, task["id"], status="pending")
                raise
            except (
                Exception
            ) as exc:  # Quelle/Format unerwartet: Aufgabe scheitert, Run läuft weiter
                failed += 1
                _set_task(
                    ctx.engine,
                    task["id"],
                    status="failed",
                    finished_at=db.utcnow(),
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                )
                ctx.note(f"Aufgabe {task['seq']} ({task['task_type']}) gescheitert: {exc}")
                continue
            _set_task(
                ctx.engine,
                task["id"],
                status="succeeded",
                finished_at=db.utcnow(),
                result=json.dumps(result, ensure_ascii=False, default=str),
                error=None,
            )
    except KeyboardInterrupt:
        _finish(ctx, run_id, "paused")
        return "paused"
    status = "completed_with_gaps" if failed or ctx.notes else "completed"
    _finish(ctx, run_id, status)
    return status


def _finish(ctx: Ctx, run_id: int, status: str) -> None:
    summary = {
        "notes": ctx.notes[-300:],
        "requests": ctx.gateway.stats.requests,
        "fetch_status": ctx.gateway.stats.by_status,
        "fetch_by_source": ctx.gateway.stats.by_source,
        "llm": ctx.llm.name,
    }
    _set_run(
        ctx.engine,
        run_id,
        status=status,
        finished_at=db.utcnow(),
        summary=json.dumps(summary, ensure_ascii=False),
    )


def _set_run(engine: Engine, run_id: int, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(update(db.research_run).where(db.research_run.c.id == run_id).values(**values))


def _set_task(engine: Engine, task_id: int, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(db.research_task).where(db.research_task.c.id == task_id).values(**values)
        )


def standard_tasks(
    compare: list[str], use_cases: list[str], country: str, out_dir: str, max_websites: int
) -> list[tuple[str, dict[str, Any]]]:
    """Aufgabenliste für Vergleich + Discovery + Bewertung + Export."""
    tasks: list[tuple[str, dict[str, Any]]] = [("profile", {"query": q}) for q in compare]
    tasks += [
        ("discover", {"use_case": u, "country": country, "max_websites": max_websites})
        for u in use_cases
    ]
    tasks += [("score", {"use_case": u}) for u in use_cases]
    tasks.append(("export", {"out_dir": out_dir}))
    return tasks
