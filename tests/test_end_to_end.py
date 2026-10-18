"""Gesamter Ablauf gegen das synthetische Test-Internet (KEIN echter Recherchenachweis).

Prüft, dass ein Lauf alle neun Nachweiskriterien technisch erfüllen kann und dass ein Lauf
ohne erreichbare Quellen sauber mit dokumentierten Lücken endet – ohne erfundene Zahlen.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select

from market_intel import db, repo
from market_intel.proof import evaluate
from market_intel.research import create_run, execute, standard_tasks
from market_intel.usecases import load_use_cases

from .fakeweb import FakeWeb

COMPARE = ["3M", "Avery Dennison", "ORAFOL"]
USE_CASES = ["fahrzeugfolierung", "glasfolien"]


def _run(make_ctx, engine, web, tmp_path):
    ctx = make_ctx(web)
    tasks = standard_tasks(COMPARE, USE_CASES, "DE", str(tmp_path / "export"), 5)
    run_id = create_run(engine, "proof", {}, tasks)
    status = execute(ctx, run_id, load_use_cases(), progress=lambda _m: None)
    return ctx, run_id, status


def test_full_run_meets_all_criteria_with_test_data(make_ctx, engine, tmp_path):
    ctx, run_id, status = _run(make_ctx, engine, FakeWeb(), tmp_path)
    assert status in ("completed", "completed_with_gaps")
    criteria = evaluate(engine, run_id, tmp_path, ctx.llm.name)
    failed = [(c.number, c.details) for c in criteria if not c.passed]
    assert failed == []

    with engine.connect() as conn:
        three_m = repo.find_company_by_identifier(conn, "lei", "TESTLEI3M0000000001")
        assert three_m is not None
        revenue = repo.observations(conn, three_m, "financial", "revenue")
        official = [r for r in revenue if r["provenance_class"] == "official"]
        derived = [r for r in revenue if r["provenance_class"] == "derived"]
        assert len(official) == 3 and all(r["source_key"] == "sec_edgar" for r in official)
        assert derived and all("EZB" in r["methodology"] for r in derived)
        # Wikidata-Umsatz 2023 (35 Mrd.) weicht von SEC (32,7 Mrd.) ab → Widerspruch sichtbar
        current = repo.current_values(conn, three_m, "financial", "revenue")
        usd_2023 = [r for r in current if r["currency"] == "USD" and r["period_end"].year == 2023]
        assert usd_2023[0]["provenance_class"] == "official" and usd_2023[0]["conflict"]

        beta = repo.find_company_by_identifier(conn, "wikidata", "Q900010")
        fits = conn.execute(
            select(db.company_product_fit.c.use_case_key, db.company_product_fit.c.fit_level).where(
                db.company_product_fit.c.company_id == beta
            )
        ).all()
        assert ("fahrzeugfolierung", "strong") in fits
        assert ("glasfolien", "strong") in fits  # zweite Seite: zwei Begriffe
        heads = repo.observations(conn, beta, "employee", "headcount_total")
        assert [h["value"] for h in heads] == [45]
        assert heads[0]["provenance_class"] == "company_reported"
        # robots.txt: /intern wurde nie abgerufen
        intern = conn.execute(
            select(db.fetch_log.c.status).where(db.fetch_log.c.url.like("%/intern"))
        ).all()
        assert intern and {r.status for r in intern} == {"blocked_robots"}
        # Ausschreibung → öffentlicher Auftraggeber mit Signal
        signals = conn.execute(select(func.count()).select_from(db.business_signal)).scalar()
        assert signals and signals >= 2

    export_files = {p.name for p in (tmp_path / "export").iterdir()}
    assert {
        "vergleich.csv",
        "chancen.csv",
        "kennzahlen.csv",
        "market_intel_export.xlsx",
        "market_intel_export.json",
        "bericht.html",
    } <= export_files
    comparison = json.loads(
        (tmp_path / "export" / "market_intel_export.json").read_text(encoding="utf-8")
    )["vergleich"]
    assert {r["name"] for r in comparison} >= {"3M Company", "ORAFOL"}


def test_run_without_internet_documents_gaps_and_invents_nothing(make_ctx, engine, tmp_path):
    ctx, run_id, status = _run(make_ctx, engine, FakeWeb(frozenset({"*"})), tmp_path)
    assert status == "completed_with_gaps"
    criteria = {c.number: c for c in evaluate(engine, run_id, tmp_path, ctx.llm.name)}
    assert criteria[1].passed and criteria[9].passed
    assert not criteria[2].passed and not criteria[4].passed and not criteria[7].passed
    with engine.connect() as conn:
        observations = sum(
            conn.execute(select(func.count()).select_from(t)).scalar() or 0
            for t in db.OBSERVATION_TABLES.values()
        )
        failures = conn.execute(
            select(func.count())
            .select_from(db.fetch_log)
            .where(db.fetch_log.c.status == "network_error")
        ).scalar()
    assert observations == 0
    assert failures and failures > 0
    run = json.loads(_summary(engine, run_id))
    assert any("fehlgeschlagen" in n for n in run["notes"])


def test_pause_and_resume(make_ctx, engine, tmp_path):
    web = FakeWeb()
    ctx = make_ctx(web)
    tasks = standard_tasks(COMPARE, USE_CASES, "DE", str(tmp_path / "export"), 5)
    run_id = create_run(engine, "proof", {}, tasks)
    seen = []

    def interrupt_on_discover(message):
        seen.append(message)
        if "discover" in message and len([m for m in seen if "discover" in m]) == 1:
            raise KeyboardInterrupt

    assert execute(ctx, run_id, load_use_cases(), progress=interrupt_on_discover) == "paused"
    with engine.connect() as conn:
        statuses = [
            r.status
            for r in conn.execute(
                select(db.research_task.c.status)
                .where(db.research_task.c.run_id == run_id)
                .order_by(db.research_task.c.seq)
            )
        ]
    assert statuses[:3] == ["succeeded"] * 3 and set(statuses[3:]) == {"pending"}
    gleif_calls = web.calls["api.gleif.org"]
    ctx2 = make_ctx(web)
    assert execute(ctx2, run_id, load_use_cases(), progress=lambda _m: None) in (
        "completed",
        "completed_with_gaps",
    )
    with engine.connect() as conn:
        statuses = {
            r.status
            for r in conn.execute(
                select(db.research_task.c.status).where(db.research_task.c.run_id == run_id)
            )
        }
    assert statuses == {"succeeded"}
    assert web.calls["api.gleif.org"] > gleif_calls  # nur die offenen Aufgaben liefen weiter


def _summary(engine, run_id):
    with engine.connect() as conn:
        return conn.execute(
            select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
        ).scalar()
