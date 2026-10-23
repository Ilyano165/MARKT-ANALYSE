"""Regressionstests aus dem Live-Test (Phase 5) und den geforderten Abnahmefällen."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
from sqlalchemy import Engine, func, select

from market_intel import db, repo
from market_intel.domain import FetchStatus
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.model import load_model
from market_intel.opportunity.run import assess_and_store
from market_intel.opportunity.watch import set_status
from market_intel.products import load_products
from market_intel.sources.user_data import import_company_list

from .fakeweb import FakeWeb
from .test_end_to_end import PROOF_REQUEST


class ProxyBlockedWeb(FakeWeb):
    """Wie die Entwicklungsumgebung im Live-Test: Proxy verweigert jeden Host mit 403."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls[request.url.host] += 1
        raise httpx.ProxyError("403 Forbidden", request=request)


def _count(engine: Engine, table: Any) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)


def test_proxy_block_is_not_retried(make_ctx: Any, engine: Engine, sleeps: list[float]) -> None:
    """Live-Befund 2026-10-23: CONNECT 403 wurde dreimal mit Wartezeit wiederholt."""
    ctx = make_ctx(ProxyBlockedWeb())
    result = ctx.gateway.get("gleif", "https://api.gleif.org/api/v1/lei-records", purpose="t")
    assert result.status is FetchStatus.NETWORK_ERROR and not result.ok
    assert "Netzwerkrichtlinie" in (result.error or "")
    with engine.connect() as conn:
        attempts = conn.execute(select(db.fetch_log.c.attempt)).scalars().all()
    assert attempts == [1] and sleeps == []  # ein Versuch, keine Wartezeit


def test_blocked_sources_produce_no_invented_results(run_research: Any, engine: Engine) -> None:
    _, run_id, status = run_research(PROOF_REQUEST, web=ProxyBlockedWeb())
    assert status in ("completed_with_gaps", "failed")
    for table in (
        db.company,
        db.financial_observation,
        db.employee_observation,
        db.company_profile_fact,
        db.corporate_event,
        db.opportunity_score,
        db.source_document,
    ):
        assert _count(engine, table) == 0, table.name
    assert lead_rows(engine) == []
    with engine.connect() as conn:
        errors = (
            conn.execute(select(db.fetch_log.c.error).where(db.fetch_log.c.run_id == run_id))
            .scalars()
            .all()
        )
    assert errors and all("403" in (e or "") for e in errors)  # jeder Fehler protokolliert


def test_customer_list_matches_by_lei_without_duplicate(engine: Engine, tmp_path: Any) -> None:
    repo.sync_sources(engine, (), (), False)
    with engine.begin() as conn:
        company_id, _ = repo.resolve_or_create(
            conn, "Beispiel Folien GmbH", [("lei", "529900T8BM49AURSDO55")], document_id=None
        )
    before = _count(engine, db.company)
    path = tmp_path / "kunden.csv"
    path.write_text("name;lei\nBeispiel Folien (Kundenliste);529900T8BM49AURSDO55\n", "utf-8")
    result = import_company_list(
        engine, path, list_name="Bestandskunden", source_url="intern:crm", publisher=None
    )
    assert result.companies == (company_id,)
    assert _count(engine, db.company) == before  # kein Duplikat


def test_customer_list_matches_by_domain_without_duplicate(engine: Engine, tmp_path: Any) -> None:
    repo.sync_sources(engine, (), (), False)
    with engine.begin() as conn:
        company_id = repo.create_company(conn, "Domain AG", website="https://www.domain-ag.test/")
    before = _count(engine, db.company)
    path = tmp_path / "kunden.csv"
    path.write_text("name;website\nDomain AG (CRM);domain-ag.test\n", "utf-8")
    result = import_company_list(
        engine, path, list_name="Bestandskunden", source_url="intern:crm", publisher=None
    )
    assert result.companies == (company_id,) and _count(engine, db.company) == before
    # zweiter Import derselben Liste: weiterhin keine Dublette
    import_company_list(
        engine, path, list_name="Bestandskunden", source_url="intern:crm", publisher=None
    )
    assert _count(engine, db.company) == before


def test_rejected_company_is_still_investigated_when_named(
    run_research: Any, engine: Engine, cases: dict[str, Any]
) -> None:
    run_research(PROOF_REQUEST)
    with engine.connect() as conn:
        orafol = repo.find_company_by_identifier(conn, "lei", "TESTLEIORAFOL0000001")
    assert orafol is not None
    set_status(engine, orafol, "verworfen", reason="kein Bedarf", author="Vertrieb")
    _, run2, _ = run_research(dataclasses.replace(PROOF_REQUEST, refresh=True))
    with engine.connect() as conn:
        selected = set(
            conn.execute(
                select(db.run_candidate.c.company_id).where(
                    db.run_candidate.c.run_id == run2, db.run_candidate.c.selected.is_(True)
                )
            ).scalars()
        )
    assert orafol in selected  # ausdrücklich genannt → wird untersucht
    best = assess_and_store(engine, [orafol], cases, load_products(), load_model())
    assert best[orafol]["segment"]["key"] == "verworfen"  # aber kein neuer Lead
    assert all(r["firma_id"] != orafol for r in lead_rows(engine))
