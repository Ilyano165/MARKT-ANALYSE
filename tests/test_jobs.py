"""Phase C: Hintergrundaufträge – echte Recherche-Pipeline über den Worker (Test-Internet).

Geprüft wird das Verhalten, nicht nur der Code: Aufträge laufen über den Worker durch die
acht Schritte, Ergebnisse landen bewertet in der Lead-Liste, Pause/Abbruch/Fortsetzen wirken,
ein abgestürzter Worker hinterlässt keinen verlorenen Auftrag, zwei Worker verarbeiten einen
Auftrag nie doppelt, und ein fehlerhafter Auftrag blockiert die anderen nicht.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, update

from market_intel import db
from market_intel.config import Settings
from market_intel.context import Ctx
from market_intel.engine.budget import Budget
from market_intel.engine.strategy import ResearchRequest
from market_intel.jobs import (
    JobError,
    Runtime,
    Worker,
    claim_next,
    create_reassess_job,
    create_research_job,
    events_of,
    get_job,
    progress,
    recover_stale,
    request_cancel,
    request_pause,
    resume_job,
    run_job,
)
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.model import load_model
from market_intel.products import load_products

from .fakeweb import FakeWeb

REQUEST = ResearchRequest(
    task="Finde Unternehmen, die Folien verarbeiten.",
    countries=["DE"],
    use_cases=["fahrzeugfolierung", "glasfolien"],
    max_candidates=10,
    years=5,
)


@pytest.fixture
def runtime(
    engine: Engine, make_ctx: Callable[..., Ctx], cases: dict[str, Any], tmp_path: Path
) -> Callable[..., Runtime]:
    def build(web: FakeWeb | None = None) -> Runtime:
        return Runtime(
            settings=Settings(database_url=str(engine.url), output_dir=tmp_path / "out"),
            cases=cases,
            catalog=load_products(),
            model=load_model(),
            ctx_factory=lambda budget, _no_llm: make_ctx(web or FakeWeb(), budget=budget),
        )

    return build


def _job(engine: Engine, tmp_path: Path, request: ResearchRequest = REQUEST, **kw: Any) -> int:
    return create_research_job(engine, request, Budget(), tmp_path / "out", **kw)


def test_research_job_runs_through_worker_and_results_reach_leads(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    job_id = _job(engine, tmp_path, title="Folienverarbeiter DE")
    job = get_job(engine, job_id)
    assert job is not None and job["status"] == "queued" and job["research_run_id"]
    assert progress(engine, job)["phase"] == "wartet"
    worker = Worker(engine, runtime())
    assert worker.run_once() == job_id
    job = get_job(engine, job_id)
    assert job is not None
    assert job["status"] in ("completed", "completed_with_gaps"), job["error"]
    prog = progress(engine, job)
    counts = prog["counts"]
    assert counts["companies_found"] >= counts["companies_selected"] > 0
    assert counts["sources_checked"] > 0 and counts["sources_ok"] > 0
    assert counts["companies_assessed"] == counts["companies_selected"]
    assert all(s["open"] == 0 for s in prog["steps"])  # alle acht Schritte abgearbeitet
    assert {s["step"] for s in prog["steps"] if s["ok"]} >= {1, 2, 3, 7, 8}
    # Ergebnisse sind sofort als Leads verfügbar – mit Belegen und aktueller Bewertung
    with engine.connect() as conn:
        selected = {
            r.company_id
            for r in conn.execute(
                select(db.run_candidate.c.company_id).where(
                    db.run_candidate.c.run_id == job["research_run_id"],
                    db.run_candidate.c.selected.is_(True),
                )
            )
        }
    leads = {
        r["firma_id"]: r for r in lead_rows(engine, include_no_fit=True, include_excluded=True)
    }
    assert selected <= set(leads)
    assert all(leads[c]["aktualitaet"] == "aktuell" for c in selected)
    assert any(leads[c]["belege"] for c in selected)
    messages = [e["message"] for e in events_of(engine, job_id)]
    assert messages[0].startswith("Auftrag erstellt") and any("Gestartet" in m for m in messages)
    assert messages[-1].startswith("Ende:")
    assert json.loads(job["result"])["counts"] == counts
    assert worker.run_once() is None  # nichts mehr in der Warteschlange


def test_all_sources_blocked_is_reported_as_failure_not_success(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    job_id = _job(engine, tmp_path)
    Worker(engine, runtime(FakeWeb(frozenset({"*"})))).run_once()
    job = get_job(engine, job_id)
    assert job is not None and job["status"] == "failed"
    counts = progress(engine, job)["counts"]
    assert counts["sources_ok"] == 0 and counts["sources_failed"] > 0
    assert any("Keine Quelle erreichbar" in e["message"] for e in events_of(engine, job_id))


def test_pause_and_resume_continue_where_stopped(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path, monkeypatch: Any
) -> None:
    import market_intel.jobs as jobs_module

    job_id = _job(engine, tmp_path)
    job = claim_next(engine, "w1")
    assert job is not None
    real_control = jobs_module._control

    def control_after_first_task(eng: Engine, jid: int) -> Callable[[], str]:
        ask = real_control(eng, jid)
        calls = {"n": 0}

        def wrapped() -> str:
            calls["n"] += 1
            if calls["n"] == 2:  # Klick auf „Anhalten“ während Schritt 1 lief
                request_pause(eng, jid)
            return ask()

        return wrapped

    monkeypatch.setattr(jobs_module, "_control", control_after_first_task)
    status = run_job(engine, job, runtime(), "w1")
    monkeypatch.setattr(jobs_module, "_control", real_control)
    assert status == "paused"
    job = get_job(engine, job_id)
    assert job is not None and job["status"] == "paused" and job["control"] == ""
    with engine.connect() as conn:
        open_tasks = conn.execute(
            select(db.research_task.c.id).where(
                db.research_task.c.run_id == job["research_run_id"],
                db.research_task.c.status == "pending",
            )
        ).all()
        done_tasks = conn.execute(
            select(db.research_task.c.task_type).where(
                db.research_task.c.run_id == job["research_run_id"],
                db.research_task.c.status == "succeeded",
            )
        ).all()
    assert open_tasks  # nachvollziehbar offen, nicht gelöscht
    assert [t.task_type for t in done_tasks] == ["plan"]  # genau Schritt 1 erledigt
    with pytest.raises(JobError):
        request_pause(engine, job_id)  # schon angehalten
    resume_job(engine, job_id)
    assert Worker(engine, runtime()).run_once() == job_id
    job = get_job(engine, job_id)
    assert job is not None and job["status"] in ("completed", "completed_with_gaps")


def test_cancel_queued_and_running(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    queued = _job(engine, tmp_path)
    assert request_cancel(engine, queued) == "cancelled"
    assert get_job(engine, queued)["status"] == "cancelled"  # type: ignore[index]
    with engine.connect() as conn:
        run_status = conn.execute(
            select(db.research_run.c.status).where(
                db.research_run.c.id == get_job(engine, queued)["research_run_id"]  # type: ignore[index]
            )
        ).scalar()
    assert run_status == "cancelled"
    running = _job(engine, tmp_path)
    job = claim_next(engine, "w1")
    assert job is not None and job["id"] == running
    assert request_cancel(engine, running) == "cancel_requested"
    assert run_job(engine, job, runtime(), "w1") == "cancelled"
    assert get_job(engine, running)["finished_at"] is not None  # type: ignore[index]
    with pytest.raises(JobError):
        resume_job(engine, running)  # abgebrochen ist endgültig


def test_crashed_worker_job_is_recovered_without_losing_progress(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    job_id = _job(engine, tmp_path)
    job = claim_next(engine, "abgestuerzt")
    assert job is not None
    # Absturz simulieren: Schritt 1 wurde erledigt, dann kein Herzschlag mehr
    from market_intel.research import execute

    rt = runtime()
    assert rt.ctx_factory is not None
    ctx = rt.ctx_factory(Budget(), True)
    calls = {"n": 0}

    def crash_after_first() -> str:
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt  # Prozess endet mitten im Auftrag
        return ""

    assert (
        execute(ctx, job["research_run_id"], rt.cases, lambda _m: None, crash_after_first)
        == "paused"
    )
    with engine.begin() as conn:
        conn.execute(
            update(db.job)
            .where(db.job.c.id == job_id)
            .values(heartbeat_at=db.utcnow() - timedelta(minutes=5))
        )
    assert recover_stale(engine) == [job_id]
    assert get_job(engine, job_id)["status"] == "queued"  # type: ignore[index]
    assert any("wieder eingereiht" in e["message"] for e in events_of(engine, job_id))
    assert Worker(engine, runtime()).run_once() == job_id
    job = get_job(engine, job_id)
    assert job is not None and job["status"] in ("completed", "completed_with_gaps")
    with engine.connect() as conn:  # Schritt 1 wurde nicht wiederholt
        plans = conn.execute(
            select(db.research_task.c.id).where(
                db.research_task.c.run_id == job["research_run_id"],
                db.research_task.c.task_type == "plan",
            )
        ).all()
    assert len(plans) == 1


def test_recovery_gives_up_after_max_attempts(engine: Engine, tmp_path: Path) -> None:
    job_id = _job(engine, tmp_path)
    with engine.begin() as conn:
        conn.execute(
            update(db.job)
            .where(db.job.c.id == job_id)
            .values(status="running", attempts=3, heartbeat_at=db.utcnow() - timedelta(hours=1))
        )
    assert recover_stale(engine) == []
    job = get_job(engine, job_id)
    assert job is not None and job["status"] == "failed" and "3 Versuchen" in job["error"]


def test_two_workers_never_claim_the_same_job(engine: Engine, tmp_path: Path) -> None:
    ids = [_job(engine, tmp_path) for _ in range(6)]
    claimed: list[int] = []
    lock = threading.Lock()

    def grab(name: str) -> None:
        while True:
            job = claim_next(engine, name)
            if job is None:
                return
            with lock:
                claimed.append(int(job["id"]))

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == ids  # jeder genau einmal


def test_failing_job_does_not_block_others(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    bad = _job(engine, tmp_path)
    good = create_reassess_job(engine)
    with engine.begin() as conn:  # kaputter Auftrag: Recherche fehlt
        conn.execute(update(db.job).where(db.job.c.id == bad).values(research_run_id=None))
    worker = Worker(engine, runtime())
    assert worker.run_once() == bad
    assert worker.run_once() == good
    assert get_job(engine, bad)["status"] == "failed"  # type: ignore[index]
    assert get_job(engine, bad)["error"]  # type: ignore[index]
    assert get_job(engine, good)["status"] == "completed"  # type: ignore[index]


def test_background_worker_thread_processes_queue(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    worker = Worker(engine, runtime(), poll_seconds=0.05)
    worker.start()
    try:
        job_id = _job(engine, tmp_path)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = get_job(engine, job_id)["status"]  # type: ignore[index]
            if status not in ("queued", "running"):
                break
            time.sleep(0.1)
        assert status in ("completed", "completed_with_gaps")
    finally:
        worker.stop()
    assert not worker.alive()


def test_empty_request_is_rejected(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(JobError):
        create_research_job(engine, ResearchRequest(task="  "), Budget(), tmp_path)


def test_keywords_reach_the_search_strategy(cases: dict[str, Any]) -> None:
    from market_intel.engine.strategy import build_strategy

    strategy = build_strategy(
        ResearchRequest(task="Finde Betriebe", keywords=["Carwrapping"]), cases
    )
    assert "Carwrapping" in strategy.web_terms and "Carwrapping" in strategy.name_terms


# ---------------------------------------------------------------------- Oberfläche (Browser)


def test_browser_creates_job_and_watches_progress_until_results(
    engine: Engine, runtime: Callable[..., Runtime], tmp_path: Path, cases: dict[str, Any]
) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    from market_intel.ui.server import make_server

    rt = runtime()
    worker = Worker(engine, rt, poll_seconds=0.2)
    worker.start()
    server = make_server(
        engine, "127.0.0.1", 0, (rt.cases, rt.catalog, rt.model), worker=worker,
        out_dir=tmp_path / "out",
    )  # fmt: skip
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium nicht installiert: {exc}")
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/jobs")
            assert "Noch keine Aufträge" in page.content()
            page.click("text=Neuer Rechercheauftrag")
            # ungültige Eingabe wird gemeldet, nichts angelegt
            page.uncheck("input[name=country][value=DE]")
            with page.expect_navigation():
                page.click("button:has-text('Auftrag starten')")
            assert "Bitte eine Aufgabenbeschreibung" in page.content()
            assert jobs_count(engine) == 0
            page.fill("#title", "Folienverarbeiter Browser")
            page.check("input[name=use_case][value=fahrzeugfolierung]")
            page.check("input[name=use_case][value=glasfolien]")
            page.check("input[name=country][value=DE]")
            page.fill("#max_candidates", "8")
            with page.expect_navigation():
                page.click("button:has-text('Auftrag starten')")
            assert "/jobs/" in page.url
            # Fortschritt läuft ohne Neuladen; am Ende lädt die Seite selbst neu
            page.wait_for_selector(
                ".status.st-ok, .status.st-warn, .status.st-err", timeout=120_000
            )
            content = page.content()
            assert "Folienverarbeiter Browser" in content
            assert "Gefundene Unternehmen" in content
            found = int(page.text_content('[data-count="companies_selected"]') or "0")
            assert found > 0
            links = page.locator("a[href$='/opportunity']")
            assert links.count() == found
            links.first.click()
            page.wait_for_load_state()
            assert "Gespeicherte Bewertung vom" in page.content()  # Phase B: gleicher Stand
            page.goto(f"{base}/jobs")
            assert "Folienverarbeiter Browser" in page.content()
            browser.close()
            assert errors == []
    finally:
        server.shutdown()
        server.server_close()
        worker.stop()


def jobs_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return len(conn.execute(select(db.job.c.id)).all())
