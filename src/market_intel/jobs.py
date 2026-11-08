"""Hintergrundaufträge: persistente Warteschlange und Worker (Phase C).

Aufbau – bewusst ohne zusätzlichen Dienst (kein Redis, keine Message-Queue):

* Aufträge stehen in der Tabelle ``job`` derselben Datenbank. Ein Rechercheauftrag verweist auf
  einen ``research_run``; dessen Teilaufgaben (``research_task``) sind bereits einzeln
  gespeichert. Ein Neustart setzt daher an der nächsten offenen Teilaufgabe fort.
* Der Worker läuft als Thread im Prozess der Anwendung (``mi ui``/Desktop) oder eigenständig
  (``mi worker``). Er übernimmt einen Auftrag **atomar** (``UPDATE … WHERE status='queued'``) –
  zwei Worker können denselben Auftrag nicht doppelt verarbeiten.
* Während der Arbeit schreibt er alle 10 s einen Herzschlag. Bleibt der Herzschlag länger als
  ``STALE_SECONDS`` aus (Absturz, Fenster geschlossen), wird der Auftrag beim nächsten Start
  wieder eingereiht (höchstens ``MAX_ATTEMPTS`` Versuche, dann „fehlgeschlagen“).
* Pause und Abbruch werden als Wunsch (``control``) gespeichert und vor der nächsten
  Teilaufgabe ausgeführt. Ein Fehler in einem Auftrag beendet nur diesen Auftrag.
* Jede Statusänderung landet im Protokoll ``job_event``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, insert, select, update

from . import db
from .engine.budget import Budget
from .engine.report import STEP_NAMES, step_overview
from .engine.strategy import ResearchRequest

log = logging.getLogger("market_intel.jobs")

STALE_SECONDS = 60
HEARTBEAT_SECONDS = 10
MAX_ATTEMPTS = 3
FINAL = ("completed", "completed_with_gaps", "budget_exhausted", "cancelled", "failed")
RESUMABLE = ("paused", "budget_exhausted", "failed")
STATUS_LABELS = {
    "queued": "wartet",
    "running": "läuft",
    "paused": "angehalten",
    "cancelled": "abgebrochen",
    "completed": "abgeschlossen",
    "completed_with_gaps": "teilweise fehlgeschlagen",
    "budget_exhausted": "Budget erschöpft – fortsetzbar",
    "failed": "fehlgeschlagen",
}
# Schritte der Recherche → verständliche Phase
PHASES = {
    1: "Suchstrategie wird erstellt",
    2: "Unternehmen werden gesucht",
    3: "Quellen werden geprüft",
    4: "Daten werden extrahiert",
    5: "Unternehmen werden abgeglichen",
    6: "Angaben werden geprüft",
    7: "Unternehmen werden bewertet",
    8: "Bericht wird erstellt",
}


class JobError(ValueError):
    """Ungültige Eingabe oder unzulässige Statusänderung."""


# ---------------------------------------------------------------------- Anlegen und Steuern


def event(engine: Engine, job_id: int, message: str, level: str = "info") -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.job_event).values(
                job_id=job_id, at=db.utcnow(), level=level, message=message[:2000]
            )
        )
    getattr(log, {"warn": "warning"}.get(level, level), log.info)("Auftrag %s: %s", job_id, message)


def create_research_job(
    engine: Engine,
    request: ResearchRequest,
    budget: Budget,
    out_dir: Path,
    *,
    title: str = "",
    no_llm: bool = True,
) -> int:
    """Rechercheauftrag anlegen (``research_run`` + ``job``) und einreihen."""
    from .research import create_research

    if not (request.task.strip() or request.use_cases or request.industries or request.companies):
        raise JobError(
            "Bitte eine Aufgabenbeschreibung, Zielgruppe, Branche oder Firmennamen angeben."
        )
    run_id = create_research(engine, request, budget, out_dir)
    title = title.strip() or request.task.strip() or ", ".join(request.use_cases)
    with engine.begin() as conn:
        job_id = int(
            conn.execute(
                insert(db.job).values(
                    kind="research",
                    title=title[:300],
                    status="queued",
                    params=json.dumps({"no_llm": no_llm}, ensure_ascii=False),
                    research_run_id=run_id,
                    control="",
                    attempts=0,
                    created_at=db.utcnow(),
                    result="{}",
                )
            ).inserted_primary_key[0]
        )
    event(engine, job_id, f"Auftrag erstellt (Recherche {run_id}) – wartet auf den Worker")
    return job_id


def create_reassess_job(engine: Engine, *, only_stale: bool = True, title: str = "") -> int:
    with engine.begin() as conn:
        job_id = int(
            conn.execute(
                insert(db.job).values(
                    kind="reassess",
                    title=title or "Veraltete Bewertungen neu berechnen",
                    status="queued",
                    params=json.dumps({"only_stale": only_stale}),
                    control="",
                    attempts=0,
                    created_at=db.utcnow(),
                    result="{}",
                )
            ).inserted_primary_key[0]
        )
    event(engine, job_id, "Neubewertung eingereiht")
    return job_id


def get_job(engine: Engine, job_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(select(db.job).where(db.job.c.id == job_id)).mappings().first()
    return dict(row) if row else None


def list_jobs(engine: Engine, limit: int = 100) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(select(db.job).order_by(db.job.c.id.desc()).limit(limit))
            .mappings()
            .all()
        ]


def events_of(engine: Engine, job_id: int, limit: int = 200) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(db.job_event)
                .where(db.job_event.c.job_id == job_id)
                .order_by(db.job_event.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in reversed(rows)]


def request_pause(engine: Engine, job_id: int) -> str:
    """Wartende Aufträge sofort anhalten, laufende vor der nächsten Teilaufgabe."""
    job = _require(engine, job_id)
    if job["status"] == "queued":
        _set(engine, job_id, status="paused", control="")
        event(engine, job_id, "Angehalten (war noch nicht gestartet)")
        return "paused"
    if job["status"] != "running":
        raise JobError(f"Auftrag ist {STATUS_LABELS[job['status']]} – Anhalten nicht möglich")
    _set(engine, job_id, control="pause")
    event(engine, job_id, "Anhalten angefordert – nach der laufenden Teilaufgabe")
    return "pause_requested"


def request_cancel(engine: Engine, job_id: int) -> str:
    job = _require(engine, job_id)
    if job["status"] in ("queued", "paused", "budget_exhausted"):
        _set(engine, job_id, status="cancelled", control="", finished_at=db.utcnow())
        _mark_run(engine, job, "cancelled")
        event(engine, job_id, "Abgebrochen")
        return "cancelled"
    if job["status"] != "running":
        raise JobError(f"Auftrag ist {STATUS_LABELS[job['status']]} – Abbruch nicht möglich")
    _set(engine, job_id, control="cancel")
    event(engine, job_id, "Abbruch angefordert – nach der laufenden Teilaufgabe")
    return "cancel_requested"


def resume_job(engine: Engine, job_id: int, **budget_limits: Any) -> None:
    """Angehaltenen, budgetbegrenzten oder fehlgeschlagenen Auftrag wieder einreihen."""
    job = _require(engine, job_id)
    if job["status"] not in RESUMABLE:
        raise JobError(f"Auftrag ist {STATUS_LABELS[job['status']]} – Fortsetzen nicht möglich")
    if job["research_run_id"] and any(v for v in budget_limits.values()):
        from .research import update_budget

        update_budget(engine, job["research_run_id"], **budget_limits)
    _set(engine, job_id, status="queued", control="", error=None, attempts=0, finished_at=None)
    event(
        engine, job_id, "Wieder eingereiht – wird an der nächsten offenen Teilaufgabe fortgesetzt"
    )


def _require(engine: Engine, job_id: int) -> dict[str, Any]:
    job = get_job(engine, job_id)
    if job is None:
        raise JobError(f"Auftrag {job_id} existiert nicht")
    return job


def _set(engine: Engine, job_id: int, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(update(db.job).where(db.job.c.id == job_id).values(**values))


def _mark_run(engine: Engine, job: dict[str, Any], status: str) -> None:
    if job.get("research_run_id"):
        with engine.begin() as conn:
            conn.execute(
                update(db.research_run)
                .where(db.research_run.c.id == job["research_run_id"])
                .values(status=status, finished_at=db.utcnow())
            )


# ---------------------------------------------------------------------- Übernahme


def claim_next(engine: Engine, worker_id: str) -> dict[str, Any] | None:
    """Ältesten wartenden Auftrag atomar übernehmen (kein doppelter Start)."""
    while True:
        with engine.connect() as conn:
            candidate = conn.execute(
                select(db.job.c.id)
                .where(db.job.c.status == "queued")
                .order_by(db.job.c.id)
                .limit(1)
            ).scalar()
        if candidate is None:
            return None
        now = db.utcnow()
        with engine.begin() as conn:
            claimed = conn.execute(
                update(db.job)
                .where(db.job.c.id == candidate, db.job.c.status == "queued")
                .values(
                    status="running",
                    worker_id=worker_id,
                    heartbeat_at=now,
                    started_at=func.coalesce(db.job.c.started_at, now),
                    attempts=db.job.c.attempts + 1,
                )
            ).rowcount
        if claimed == 1:
            return get_job(engine, int(candidate))
        # ein anderer Worker war schneller – nächsten versuchen


def recover_stale(engine: Engine, stale_seconds: int = STALE_SECONDS) -> list[int]:
    """Laufende Aufträge ohne Herzschlag (Absturz, Fenster geschlossen) wieder einreihen."""
    from datetime import timedelta

    limit = db.utcnow() - timedelta(seconds=stale_seconds)
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.job.c.id, db.job.c.attempts, db.job.c.heartbeat_at).where(
                db.job.c.status == "running"
            )
        ).all()
    recovered = []
    for job_id, attempts, beat in rows:
        if beat is not None and _aware(beat) > limit:
            continue
        if attempts >= MAX_ATTEMPTS:
            _set(
                engine,
                job_id,
                status="failed",
                finished_at=db.utcnow(),
                error=f"nach {attempts} Versuchen ohne Abschluss abgebrochen",
            )
            event(engine, job_id, f"Nach {attempts} unterbrochenen Versuchen aufgegeben", "error")
            continue
        _set(engine, job_id, status="queued", worker_id=None, control="")
        event(
            engine,
            job_id,
            "Worker wurde unterbrochen (Programm beendet oder abgestürzt) – Auftrag wieder "
            "eingereiht, Fortschritt bleibt erhalten",
            "warn",
        )
        recovered.append(int(job_id))
    return recovered


def _aware(value: Any) -> Any:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------- Fortschritt


def progress(engine: Engine, job: dict[str, Any]) -> dict[str, Any]:
    """Fortschritt aus den tatsächlich gespeicherten Daten (keine geschätzten Zahlen)."""
    status = job["status"]
    out: dict[str, Any] = {
        "job_id": job["id"],
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "control": job["control"],
        "phase": STATUS_LABELS.get(status, status),
        "steps": [],
        "counts": {},
        "result": json.loads(job["result"] or "{}"),
        "error": job["error"],
    }
    run_id = job.get("research_run_id")
    if job["kind"] == "research" and run_id:
        steps = step_overview(engine, run_id)
        out["steps"] = [
            {
                "step": s["step"],
                "name": STEP_NAMES[s["step"]],
                "ok": s.get("succeeded", 0),
                "failed": s.get("failed", 0),
                "skipped": s.get("skipped", 0),
                "open": s.get("pending", 0) + s.get("running", 0),
            }
            for s in steps
        ]
        active = next((s for s in out["steps"] if s["open"]), None)
        if status == "running":
            out["phase"] = PHASES[active["step"]] if active else "Recherche läuft"
        with engine.connect() as conn:
            found = conn.execute(
                select(func.count(func.distinct(db.run_candidate.c.company_id))).where(
                    db.run_candidate.c.run_id == run_id
                )
            ).scalar()
            selected = conn.execute(
                select(func.count(func.distinct(db.run_candidate.c.company_id))).where(
                    db.run_candidate.c.run_id == run_id, db.run_candidate.c.selected.is_(True)
                )
            ).scalar()
            fetches: dict[str, int] = {
                str(st): int(n)
                for st, n in conn.execute(
                    select(db.fetch_log.c.status, func.count())
                    .where(db.fetch_log.c.run_id == run_id)
                    .group_by(db.fetch_log.c.status)
                ).all()
            }
            assessed = conn.execute(
                select(func.count(func.distinct(db.opportunity_score.c.company_id))).where(
                    db.opportunity_score.c.research_run_id == run_id
                )
            ).scalar()
        ok = sum(v for k, v in fetches.items() if k in ("ok", "cached", "not_modified"))
        out["counts"] = {
            "companies_found": int(found or 0),
            "companies_selected": int(selected or 0),
            "sources_checked": int(sum(fetches.values())),
            "sources_ok": int(ok),
            "sources_failed": int(sum(fetches.values()) - ok),
            "companies_assessed": int(assessed or 0),
        }
    return out


# ---------------------------------------------------------------------- Ausführung


@dataclass
class Runtime:
    """Was der Worker zum Ausführen braucht (aus Einstellungen geladen)."""

    settings: Any
    cases: dict[str, Any]
    catalog: Any
    model: Any
    # (Budget, ohne KI) → Ctx; Standard: echtes Gateway aus den Einstellungen (Tests: Test-Web)
    ctx_factory: Callable[[Budget, bool], Any] | None = None


def run_job(engine: Engine, job: dict[str, Any], runtime: Runtime, worker_id: str) -> str:
    """Einen übernommenen Auftrag ausführen. Fehler beenden nur diesen Auftrag."""
    job_id = int(job["id"])
    stop_beat = threading.Event()
    beat = threading.Thread(
        target=_heartbeat, args=(engine, job_id, worker_id, stop_beat), daemon=True
    )
    beat.start()
    event(engine, job_id, f"Gestartet (Versuch {job['attempts']}, Worker {worker_id})")
    try:
        if job["kind"] == "research":
            status, result = _run_research(engine, job, runtime)
        elif job["kind"] == "reassess":
            status, result = _run_reassess(engine, job, runtime)
        else:
            raise JobError(f"unbekannte Auftragsart {job['kind']!r}")
    except Exception as exc:
        status, result = "failed", {}
        _set(engine, job_id, error=f"{type(exc).__name__}: {exc}"[:2000])
        event(engine, job_id, f"Fehlgeschlagen: {type(exc).__name__}: {exc}", "error")
        log.error("Auftrag %s fehlgeschlagen\n%s", job_id, traceback.format_exc())
    finally:
        stop_beat.set()
        beat.join(timeout=5)
    final: dict[str, Any] = {
        "status": status,
        "control": "",
        "result": json.dumps(result, ensure_ascii=False, default=str),
    }
    if status in FINAL:
        final["finished_at"] = db.utcnow()
    _set(engine, job_id, **final)
    event(
        engine,
        job_id,
        f"Ende: {STATUS_LABELS.get(status, status)}",
        "error" if status == "failed" else ("warn" if status != "completed" else "info"),
    )
    return status


def _heartbeat(engine: Engine, job_id: int, worker_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            _set(engine, job_id, heartbeat_at=db.utcnow())
        except Exception:  # Datenbank kurz gesperrt – nächster Versuch in 10 s
            log.warning("Herzschlag für Auftrag %s nicht geschrieben", job_id)


def _control(engine: Engine, job_id: int) -> Callable[[], str]:
    def ask() -> str:
        with engine.connect() as conn:
            return str(
                conn.execute(select(db.job.c.control).where(db.job.c.id == job_id)).scalar() or ""
            )

    return ask


def _run_research(
    engine: Engine, job: dict[str, Any], runtime: Runtime
) -> tuple[str, dict[str, Any]]:
    from .cli import make_ctx
    from .research import execute, run_parameters

    run_id = int(job["research_run_id"])
    params = json.loads(job["params"] or "{}")
    budget = Budget.from_dict(run_parameters(engine, run_id).get("budget", {}))
    no_llm = bool(params.get("no_llm", True))
    ctx = (
        runtime.ctx_factory(budget, no_llm)
        if runtime.ctx_factory is not None
        else make_ctx(runtime.settings, engine, budget, no_llm=no_llm)
    )
    job_id = int(job["id"])

    def on_progress(line: str) -> None:
        if "FEHLER" in line:
            event(engine, job_id, line, "warn")

    try:
        status = execute(ctx, run_id, runtime.cases, on_progress, _control(engine, job_id))
    finally:
        ctx.gateway.close()
    with engine.connect() as conn:
        summary = json.loads(
            conn.execute(
                select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
            ).scalar()
            or "{}"
        )
    counts = progress(engine, {**job, "status": status})["counts"]
    for note in summary.get("notes", [])[-20:]:
        event(engine, job_id, f"Hinweis: {note}", "warn")
    # Ehrlicher Endstatus: nichts gefunden und Quellen gescheitert ist kein Erfolg
    if status in ("completed", "completed_with_gaps") and counts.get("companies_selected", 0) == 0:
        if counts.get("sources_failed", 0) and not counts.get("sources_ok", 0):
            status = "failed"
            event(engine, job_id, "Keine Quelle erreichbar und kein Unternehmen gefunden", "error")
        else:
            event(engine, job_id, "Kein passendes Unternehmen gefunden", "warn")
    return status, {"counts": counts, "research_run_id": run_id}


def _run_reassess(
    engine: Engine, job: dict[str, Any], runtime: Runtime
) -> tuple[str, dict[str, Any]]:
    from .opportunity.current import refresh

    params = json.loads(job["params"] or "{}")
    job_id = int(job["id"])
    control = _control(engine, job_id)
    state = {"n": 0}

    class Stop(Exception):
        pass

    def on_progress(done: int, total: int) -> None:
        state["n"] = done
        if done == 1 or done % 25 == 0 or done == total:
            _set(engine, job_id, result=json.dumps({"done": done, "total": total}))
        if control() in ("pause", "cancel"):
            raise Stop

    try:
        result = refresh(
            engine,
            runtime.cases,
            runtime.catalog,
            runtime.model,
            only_stale=bool(params.get("only_stale", True)),
            trigger=f"auftrag-{job_id}",
            progress=on_progress,
        )
    except Stop:
        requested = control()
        event(engine, job_id, f"Nach {state['n']} Firmen beendet ({requested})")
        return ("paused" if requested == "pause" else "cancelled"), {"done": state["n"]}
    event(
        engine,
        job_id,
        f"{result['reassessed']} von {result['stale']} veralteten Bewertungen neu berechnet",
    )
    return "completed", result


# ---------------------------------------------------------------------- Worker


class Worker:
    """Verarbeitet Aufträge nacheinander in einem Hintergrund-Thread."""

    def __init__(self, engine: Engine, runtime: Runtime, poll_seconds: float = 1.0) -> None:
        self.engine = engine
        self.runtime = runtime
        self.poll_seconds = poll_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{id(self) % 10_000}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: int | None = None

    def start(self) -> None:
        recovered = recover_stale(self.engine)
        if recovered:
            log.info("Wieder eingereiht: %s", recovered)
        self._thread = threading.Thread(target=self._loop, name="mi-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Keine neuen Aufträge mehr; ein laufender wird angehalten (fortsetzbar)."""
        self._stop.set()
        if self.current is not None:
            job = get_job(self.engine, self.current)
            if job and job["status"] == "running" and not job["control"]:
                _set(self.engine, self.current, control="pause")
                event(self.engine, self.current, "Programm wird beendet – Auftrag wird angehalten")
        if self._thread is not None:
            self._thread.join(timeout)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> int | None:
        """Einen Auftrag übernehmen und ausführen (für Tests und ``mi worker --once``)."""
        job = claim_next(self.engine, self.worker_id)
        if job is None:
            return None
        self.current = int(job["id"])
        try:
            run_job(self.engine, job, self.runtime, self.worker_id)
        finally:
            self.current = None
        return int(job["id"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                done = self.run_once()
            except Exception:  # Worker darf nie sterben
                log.exception("Worker-Fehler")
                done = None
            if done is None:
                self._stop.wait(self.poll_seconds)


def load_runtime(settings: Any, cases: dict[str, Any]) -> Runtime:
    from .opportunity.model import load_model
    from .products import load_products

    return Runtime(
        settings=settings,
        cases=cases,
        catalog=load_products(settings.products_file),
        model=load_model(settings.opportunity_file),
    )
