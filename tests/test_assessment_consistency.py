# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
"""Phase B: ein Bewertungsstand für Lead-Liste, Opportunity-Seite und Export.

Audit P1-4: Avery Dennison stand in der Lead-Liste mit Fit 81 (gespeichert, Modell v2) und auf
der Opportunity-Seite mit Fit 40 (live, Modell v3).
"""

from __future__ import annotations

import dataclasses
import json
import re
import threading
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select, update

from market_intel import db, repo
from market_intel.intel import manual
from market_intel.opportunity.assess import latest_assessments, store_assessment
from market_intel.opportunity.current import freshness, refresh, stale_ids
from market_intel.opportunity.leads import export_leads, lead_rows
from market_intel.opportunity.report import opportunity_report
from market_intel.opportunity.run import assess_and_store
from market_intel.sources.user_data import store_document

from .test_opportunity import world  # noqa: F401  (Fixture)


def _ctx(world: dict[str, Any]) -> tuple[Any, Any, Any]:
    return world["cases"], world["catalog"], world["model"]


def _lead(engine: Engine, world: dict[str, Any], cid: int) -> dict[str, Any]:
    rows = lead_rows(engine, include_no_fit=True, include_excluded=True, context=_ctx(world))
    return next(r for r in rows if r["firma_id"] == cid)


def _age_to_v2(engine: Engine, cid: int, fit: int = 81, priority: int = 70) -> None:
    """Gespeicherte Runde so verändern, wie sie vor Modell v3 aussah (alte Version, alter Fit)."""
    with engine.begin() as conn:
        for row in conn.execute(
            select(db.opportunity_score).where(db.opportunity_score.c.company_id == cid)
        ).mappings():
            details = json.loads(row["details"])
            details.pop("basis", None)
            details["model_version"] = "v2-2026-10-20"
            conn.execute(
                update(db.opportunity_score)
                .where(db.opportunity_score.c.id == row["id"])
                .values(
                    model_version="v2-2026-10-20",
                    product_relevance=fit,
                    priority_score=priority,
                    details=json.dumps(details),
                )
            )


def test_fresh_assessments_are_current_and_carry_their_basis(
    world: dict[str, Any], engine: Engine
) -> None:
    fresh = freshness(engine, *_ctx(world))
    assert set(fresh) == set(world["ids"].values())
    assert all(f.current for f in fresh.values()), {k: f.label for k, f in fresh.items()}
    row = latest_assessments(engine, world["ids"]["folierer"])[0]
    details = json.loads(row["details"])
    basis = details["basis"]
    assert basis["model_version"] == world["model"].version
    assert len(basis["fingerprint"]) == 20 and basis["rules_digest"]
    assert basis["data"]["employee_observation"][0] == 2  # zwei belegte Mitarbeiterzahlen
    assert details["trigger"] == "bewertung"
    assert _lead(engine, world, world["ids"]["folierer"])["aktualitaet"] == "aktuell"


def test_old_model_is_marked_stale_and_never_shown_as_current(
    world: dict[str, Any], engine: Engine
) -> None:
    cid = world["ids"]["folierer"]
    before = _lead(engine, world, cid)
    _age_to_v2(engine, cid)
    stale = _lead(engine, world, cid)
    assert stale["produktrelevanz"] == 81 and stale["modell"] == "v2-2026-10-20"
    assert stale["aktualitaet"].startswith("veraltet – älteres Bewertungsmodell")
    rows = lead_rows(engine, include_no_fit=True, include_excluded=True, context=_ctx(world))
    assert rows[-1]["firma_id"] == cid  # nach allen aktuellen einsortiert, trotz Priorität 70
    # Opportunity-Seite: bewertet diese Firma neu, speichert, und zeigt denselben Stand
    report = opportunity_report(engine, cid, *_ctx(world))
    assert report["assessment_state"]["trigger"] == "opportunity-seite"
    assert "älteres Bewertungsmodell" in report["assessment_state"]["refreshed_because"]
    after = _lead(engine, world, cid)
    assert after["aktualitaet"] == "aktuell"
    assert after["produktrelevanz"] == report["best"]["scores"]["product_relevance"]
    assert after["prioritaet"] == report["best"]["scores"]["priority"]
    assert after["produktrelevanz"] == before["produktrelevanz"]  # Ergebnis reproduzierbar
    with engine.connect() as conn:  # Historie bleibt: alte Runde + neue Runde
        n = conn.execute(
            select(db.opportunity_score.c.id).where(db.opportunity_score.c.company_id == cid)
        ).all()
    assert len(n) > len(latest_assessments(engine, cid))


def test_report_page_lead_list_and_export_agree_for_every_company(
    world: dict[str, Any], engine: Engine, tmp_path: Path
) -> None:
    for cid in world["ids"].values():
        _age_to_v2(engine, cid)
    for cid in world["ids"].values():
        opportunity_report(engine, cid, *_ctx(world))
    export_leads(engine, tmp_path, include_no_fit=True, include_excluded=True, context=_ctx(world))
    exported = {
        r["firma_id"]: r
        for r in json.loads((tmp_path / "leads.json").read_text(encoding="utf-8"))["leads"]
    }
    for cid in world["ids"].values():
        report = opportunity_report(engine, cid, *_ctx(world))
        lead = _lead(engine, world, cid)
        for source in (lead, exported[cid]):
            assert source["produktrelevanz"] == report["best"]["scores"]["product_relevance"]
            assert source["prioritaet"] == report["best"]["scores"]["priority"]
            assert source["aktualitaet"] == "aktuell"
        # zweiter Aufruf bewertet nicht erneut (Stand aktuell)
        assert report["assessment_state"]["refreshed_because"] is None


def test_new_company_data_makes_assessment_stale_notes_do_not(
    world: dict[str, Any], engine: Engine
) -> None:
    cid = world["ids"]["folien_klein"]
    with engine.begin() as conn:
        repo.add_note(conn, cid, "notes", "Rückruf vereinbart", "Vertrieb")
    assert freshness(engine, *_ctx(world))[cid].current  # Notiz ändert keine Bewertung
    doc = store_document(
        engine, "user_upload", b"Folierwerkstatt Mini 2024: 5 Mitarbeiter.",
        url="upload:neu.txt", title="Abschluss 2024", publisher=None, published=None,
        content_type="text/plain",
    )  # fmt: skip
    manual.add_figure(
        engine, company_id=cid, document_id=doc, quote="Folierwerkstatt Mini 2024: 5 Mitarbeiter.",
        locator="Test", metric="headcount_total", value=Decimal(5),
        period_end=date(2024, 12, 31), provenance="official",
    )  # fmt: skip
    state = freshness(engine, *_ctx(world))[cid]
    assert state.status == "daten" and "neue oder geänderte Firmendaten" in state.label
    assert stale_ids(freshness(engine, *_ctx(world))) == [cid]
    result = refresh(engine, *_ctx(world), trigger="test")
    assert result["stale"] == 1 and result["reassessed"] == 1
    assert freshness(engine, *_ctx(world))[cid].current
    assert json.loads(latest_assessments(engine, cid)[0]["details"])["trigger"] == "test"
    assert refresh(engine, *_ctx(world))["reassessed"] == 0  # nichts mehr veraltet


def test_changed_rules_make_all_assessments_stale(world: dict[str, Any], engine: Engine) -> None:
    model = world["model"]
    changed = dataclasses.replace(model, raw={**model.raw, "test_änderung": {"x": 1}})
    fresh = freshness(engine, world["cases"], world["catalog"], changed)
    assert {f.status for f in fresh.values()} == {"regeln"}


def test_older_run_never_overwrites_newer_result(world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["glas"]
    newest = latest_assessments(engine, cid)[0]
    future = db.utcnow() + timedelta(hours=1)
    with engine.begin() as conn:  # eine Runde mit jüngerem Datenstand existiert bereits
        details = json.loads(newest["details"])
        details["batch"] = "zukunft"
        store_assessment(conn, details, None, assessed_at=future)
    stored = assess_and_store(engine, [cid], *_ctx(world), trigger="langsamer-lauf")
    assert stored == {}  # nicht gespeichert
    rows = latest_assessments(engine, cid)
    assert {json.loads(r["details"])["batch"] for r in rows} == {"zukunft"}


def test_batch_is_written_atomically(world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["folierer"]
    assess_and_store(engine, [cid], *_ctx(world))
    rows = latest_assessments(engine, cid)
    assert len({r["assessed_at"] for r in rows}) == 1  # ein Datenstand je Runde
    assert len({json.loads(r["details"])["batch"] for r in rows}) == 1


def test_ui_marks_stale_and_refresh_button_reassesses(
    world: dict[str, Any], engine: Engine
) -> None:
    from market_intel.ui.server import make_server

    cid = world["ids"]["folierer"]
    _age_to_v2(engine, cid)
    server = make_server(engine, "127.0.0.1", 0, _ctx(world))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/leads").read().decode()
        assert "1 von " in page and "Bewertungen sind veraltet" in page
        assert "veraltet – älteres Bewertungsmodell" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        data = urllib.parse.urlencode({"csrf": token, "back": "/leads"}).encode()
        page = urllib.request.urlopen(f"{base}/assessments/refresh", data=data).read().decode()
        assert "Bewertungen sind aktuell" in page and "älteres Bewertungsmodell" not in page
        assert json.loads(latest_assessments(engine, cid)[0]["details"])["trigger"] == "lead-liste"
        page = urllib.request.urlopen(f"{base}/company/{cid}/opportunity").read().decode()
        assert 'id="assessment-state"' in page and "Anlass: lead-liste" in page
        csv = urllib.request.urlopen(f"{base}/leads.csv").read().decode("utf-8-sig")
        assert "aktualitaet" in csv.splitlines()[0]
    finally:
        server.shutdown()
        server.server_close()


def test_cli_reassess_check_and_run(world: dict[str, Any], engine: Engine, capsys: Any) -> None:
    from market_intel.cli import main

    cid = world["ids"]["bank"]
    _age_to_v2(engine, cid)
    url = str(engine.url)
    assert main(["--db", url, "reassess", "--check"]) == 0
    out = capsys.readouterr().out
    assert "veraltet – älteres Bewertungsmodell" in out
    assert not freshness(engine, *_ctx(world))[cid].current  # --check ändert nichts
    assert main(["--db", url, "reassess"]) == 0
    assert "Neu bewertet: 1" in capsys.readouterr().out
    assert freshness(engine, *_ctx(world))[cid].current


def test_rules_digest_is_identical_across_processes() -> None:
    """Regression (Phase G): ``repr(frozenset)`` hängt vom Hash-Seed ab – nach jedem Neustart
    der Anwendung galten alle Bewertungen als „veraltet – Regeln geändert“."""
    import os
    import subprocess
    import sys

    code = (
        "from market_intel.products import load_products;"
        "from market_intel.usecases import load_use_cases;"
        "from market_intel.opportunity.model import load_model;"
        "from market_intel.opportunity.current import rules_digest;"
        "print(rules_digest(load_use_cases(), load_products(), load_model()))"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    digests = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": src},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2", "3", "4")
    }
    assert len(digests) == 1, digests
