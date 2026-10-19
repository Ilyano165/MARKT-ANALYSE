"""Gesamter Ablauf der Research Engine gegen das synthetische Test-Internet.

KEIN Nachweis echter Recherche (dafür: manueller Integrationstest, tests/manual).
"""

from __future__ import annotations

import json
import re

from sqlalchemy import func, select, text

from market_intel import db, repo
from market_intel.engine.budget import Budget
from market_intel.engine.strategy import ResearchRequest
from market_intel.proof import evaluate
from market_intel.research import create_research, execute, named_companies, update_budget

from .fakeweb import FakeWeb

COMPARE = ["3M", "Avery Dennison", "ORAFOL"]
PROOF_REQUEST = ResearchRequest(
    task="Vergleiche die Entwicklung der Folienhersteller und finde Unternehmen, die Folien "
    "verarbeiten.",
    countries=["DE"],
    companies=COMPARE,
    use_cases=["fahrzeugfolierung", "glasfolien"],
    max_candidates=10,
    years=5,
)


def _count(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar() or 0


def test_full_pipeline_all_steps_and_criteria(run_research, engine, tmp_path):
    ctx, run_id, status = run_research(PROOF_REQUEST)
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        steps = {
            r.step
            for r in conn.execute(
                select(db.research_task.c.step).where(db.research_task.c.run_id == run_id)
            )
        }
    assert steps == set(range(1, 9))
    failed = [
        (c.number, c.details)
        for c in evaluate(engine, run_id, tmp_path, ctx.ai.name)
        if not c.passed
    ]
    assert failed == []

    with engine.connect() as conn:
        orafol = repo.find_company_by_identifier(conn, "lei", "TESTLEIORAFOL0000001")
        revenue = repo.observations(conn, orafol, "financial", "revenue")
        assert [
            (r["value"], r["currency"], r["period_end"].year, r["source_key"]) for r in revenue
        ] == [(812500000, "EUR", 2023, "website")]
        heads = {
            (r["value"], r["period_end"].isoformat(), r["source_key"])
            for r in repo.observations(conn, orafol, "employee", "headcount_total")
        }
        assert (2950, "2023-12-31", "website") in heads  # Stichtag aus dem PDF
        ids = repo.identifiers_of(conn, orafol)
        assert ids["de_register"] == "Neuruppin HRB 12345 NP" and ids["vat"] == "DE123456789"
        hiring = (
            conn.execute(
                select(db.hiring_observation).where(db.hiring_observation.c.company_id == orafol)
            )
            .mappings()
            .one()
        )
        assert hiring["open_positions_count"] == 2
        assert "fahrzeugfolierung" in json.loads(hiring["relevant_use_cases"])
        signals = repo.signals_for(conn, orafol)
        assert any(
            s["signal_type"] == "new_location" and str(s["event_date"]) == "2026-05-12"
            for s in signals
        )
        # Prompt-Injection im PDF erzeugt keinen Wert, sondern einen Prüfhinweis
        values = [r["value"] for r in revenue]
        assert 9_000_000_000 not in values
        assert (
            conn.execute(
                select(db.validation_issue).where(
                    db.validation_issue.c.code == "revenue_without_period",
                    db.validation_issue.c.quote.like("%Ignoriere%"),
                )
            ).first()
            is not None
        )
        # robots.txt (/shop/) und fremde Domain (evil.test) wurden nicht abgerufen
        urls = {
            r.url: r.status for r in conn.execute(select(db.fetch_log.c.url, db.fetch_log.c.status))
        }
        assert not any("evil.test" in u for u in urls)
        assert all(v == "blocked_robots" for u, v in urls.items() if "/shop/" in u)

    report = (tmp_path / "out" / "bericht.md").read_text(encoding="utf-8")
    assert "## 7. Quellenverzeichnis" in report and "[Berechnung" in report
    # Jede Kennzahlzeile im Bericht hat einen Quellenverweis
    fact_lines = [
        line for line in report.splitlines() if re.match(r"\s+- \d{4}-\d{2}-\d{2}: ", line)
    ]
    assert fact_lines and all(re.search(r"\[Q\d+\]", line) for line in fact_lines)
    # Veröffentlichungsdatum wird nie erfunden
    assert "veröffentlicht unbekannt" in report


def test_failing_adapter_does_not_break_the_run(run_research, engine):
    web = FakeWeb(frozenset({"data.sec.gov", "www.orafol-test.test"}))
    _ctx, run_id, status = run_research(PROOF_REQUEST, web=web)
    assert status == "completed_with_gaps"
    assert (
        _count(
            engine,
            db.research_task,
            db.research_task.c.run_id == run_id,
            db.research_task.c.status == "failed",
        )
        >= 1
    )
    assert (
        _count(
            engine,
            db.research_task,
            db.research_task.c.task_type == "report",
            db.research_task.c.status == "succeeded",
        )
        == 1
    )
    with engine.connect() as conn:
        three_m = repo.find_company_by_identifier(conn, "lei", "TESTLEI3M0000000001")
        assert repo.observations(conn, three_m, "employee", "headcount_total")  # Wikidata lief


def test_run_without_internet_invents_nothing(run_research, engine, tmp_path):
    ctx, run_id, status = run_research(PROOF_REQUEST, web=FakeWeb(frozenset({"*"})))
    assert status == "completed_with_gaps"
    assert sum(_count(engine, t) for t in db.OBSERVATION_TABLES.values()) == 0
    # Aufgaben, deren Abrufe alle scheiterten, sind als fehlgeschlagen markiert – nicht als ok
    with engine.connect() as conn:
        statuses = {r.label: (r.status, r.error) for r in conn.execute(select(
            db.research_task.c.label, db.research_task.c.status, db.research_task.c.error).where(
            db.research_task.c.run_id == run_id, db.research_task.c.step == 2))}
    assert all(st == "failed" and "gescheitert" in (err or "")
               for label, (st, err) in statuses.items() if label.startswith("Genanntes"))
    criteria = {c.number: c for c in evaluate(engine, run_id, tmp_path, ctx.ai.name)}
    assert criteria[1].passed and criteria[9].passed and not criteria[2].passed


def test_pause_and_resume_continue_where_stopped(make_ctx, engine, tmp_path, cases):
    web = FakeWeb()
    budget = Budget()
    run_id = create_research(engine, PROOF_REQUEST, budget, tmp_path / "out")
    seen = []

    def interrupt_in_step_three(message):
        seen.append(message)
        if "[Schritt 3/8" in message and sum("[Schritt 3/8" in m for m in seen) == 2:
            raise KeyboardInterrupt

    assert (
        execute(make_ctx(web, budget=budget), run_id, cases, progress=interrupt_in_step_three)
        == "paused"
    )
    done_before = _count(
        engine,
        db.research_task,
        db.research_task.c.run_id == run_id,
        db.research_task.c.status.in_(["succeeded", "skipped", "failed"]),
    )
    calls_before = sum(web.calls.values())
    status = execute(make_ctx(web, budget=budget), run_id, cases, progress=lambda _m: None)
    assert status in ("completed", "completed_with_gaps")
    assert (
        _count(
            engine,
            db.research_task,
            db.research_task.c.run_id == run_id,
            db.research_task.c.status.in_(["pending", "running"]),
        )
        == 0
    )
    # erledigte Aufgaben liefen nicht erneut: Plan nur einmal ausgeführt
    with engine.connect() as conn:
        attempts = conn.execute(
            select(db.research_task.c.attempts).where(
                db.research_task.c.run_id == run_id, db.research_task.c.task_type == "plan"
            )
        ).scalar()
    assert attempts == 1 and done_before > 3 and sum(web.calls.values()) > calls_before


def test_budget_exhausted_and_resumed_with_higher_budget(make_ctx, engine, tmp_path, cases):
    web = FakeWeb()
    small = Budget(max_requests=6)
    run_id = create_research(engine, PROOF_REQUEST, small, tmp_path / "out")
    status = execute(make_ctx(web, budget=small), run_id, cases, progress=lambda _m: None)
    assert status == "budget_exhausted"
    blocked = _count(engine, db.fetch_log, db.fetch_log.c.status == "blocked_policy")
    assert sum(web.calls.values()) <= 6 + 1 or blocked >= 1
    bigger = update_budget(engine, run_id, max_requests=2000)
    status = execute(make_ctx(web, budget=bigger), run_id, cases, progress=lambda _m: None)
    assert status in ("completed", "completed_with_gaps")


def test_task_limit_stops_run(run_research):
    _, _, status = run_research(PROOF_REQUEST, budget=Budget(max_tasks=3))
    assert status == "budget_exhausted"


def test_second_run_skips_recently_investigated_sources(run_research, engine):
    run_research(PROOF_REQUEST)
    web = FakeWeb()
    _, run_id, _ = run_research(PROOF_REQUEST, web=web)
    skipped = _count(
        engine,
        db.research_task,
        db.research_task.c.run_id == run_id,
        db.research_task.c.task_type == "investigate",
        db.research_task.c.status == "skipped",
    )
    assert skipped >= 3
    assert web.calls["www.orafol-test.test"] == 0  # Website nicht erneut abgerufen
    # Extraktion nutzt die gespeicherten Dokumente weiter
    assert (
        _count(
            engine,
            db.research_task,
            db.research_task.c.run_id == run_id,
            db.research_task.c.task_type == "extract",
            db.research_task.c.status == "succeeded",
        )
        >= 1
    )


def test_unreachable_source_later_keeps_observations_unchanged(run_research, engine, tmp_path):
    run_research(PROOF_REQUEST)
    with engine.connect() as conn:
        before = conn.execute(
            select(db.financial_observation.c.id, db.financial_observation.c.verification_status)
        ).all()
    request = ResearchRequest(
        task="Entwicklung", companies=["ORAFOL"], countries=["DE"], refresh=True
    )
    _, _run_id, _ = run_research(request, web=FakeWeb(frozenset({"www.orafol-test.test"})))
    with engine.connect() as conn:
        after = conn.execute(
            select(db.financial_observation.c.id, db.financial_observation.c.verification_status)
        ).all()
    assert set(before) <= set(after)
    report = (tmp_path / "out" / "bericht.md").read_text(encoding="utf-8")
    assert "nicht erreichbar" in report and "Aktualität unbekannt" in report


def test_conflicting_figures_are_flagged(run_research, engine):
    _, run_id, _ = run_research(PROOF_REQUEST)
    with engine.connect() as conn:
        three_m = repo.find_company_by_identifier(conn, "lei", "TESTLEI3M0000000001")
        current = repo.current_values(conn, three_m, "financial", "revenue")
        usd_2023 = [r for r in current if r["currency"] == "USD" and r["period_end"].year == 2023]
        assert usd_2023[0]["provenance_class"] == "official" and usd_2023[0]["conflict"]
        issue = conn.execute(
            select(db.validation_issue).where(
                db.validation_issue.c.research_run_id == run_id,
                db.validation_issue.c.code == "conflicting_sources",
            )
        ).first()
    assert issue is not None


def test_duplicates_become_match_candidates(run_research, engine):
    request = ResearchRequest(
        task="Finde Werbetechnik-Betriebe in Deutschland",
        countries=["DE"],
        use_cases=["werbetechnik_beschriftung"],
    )
    run_research(request)
    with engine.connect() as conn:
        rows = conn.execute(select(db.match_candidate)).mappings().all()
        names = {repo.company_row(conn, r["company_a_id"])["display_name"] for r in rows} | {
            repo.company_row(conn, r["company_b_id"])["display_name"] for r in rows
        }
    assert any(r["kind"] == "possible_duplicate" for r in rows)
    assert {"Beta Werbetechnik", "Beta Werbetechnik GmbH"} <= names


def test_named_companies_resolved_and_comparison_only(run_research, engine):
    request = ResearchRequest(
        task="Vergleiche die Unternehmensentwicklung von „3M“ und "
        "„Avery Dennison“ über die letzten fünf Jahre"
    )
    _, run_id, _ = run_research(request)
    assert len(named_companies(engine, run_id)) == 2
    assert (
        _count(
            engine,
            db.research_task,
            db.research_task.c.run_id == run_id,
            db.research_task.c.task_type == "find",
        )
        == 0
    )


def test_append_only_still_enforced_after_pipeline(run_research, engine):
    run_research(PROOF_REQUEST)
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("UPDATE financial_observation SET value='1'"))
