# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
"""Vertriebsergebnisse: Erfassung, Unveränderlichkeit, Wirkung auf Leads, Auswertung aus Daten."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from market_intel import db, repo
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.outcomes import (
    OutcomeError,
    current_outcome,
    evaluate,
    evaluation_markdown,
    outcome_history,
    record_outcome,
)
from market_intel.opportunity.run import assess_and_store
from market_intel.opportunity.watch import excluded_reason

from .test_opportunity import world  # noqa: F401  (Fixture: Testfirmen aus Phase 4)

TODAY = date.today()


def _assess(engine: Engine, world: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return assess_and_store(
        engine, list(world["ids"].values()), world["cases"], world["catalog"], world["model"]
    )


def test_record_history_and_validation(world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["folierer"]
    record_outcome(engine, cid, "kontakt", occurred_on=TODAY - timedelta(days=9), author="V")
    record_outcome(
        engine, cid, "angebot", occurred_on=TODAY, author="V", value="12.500,00".replace(".", ""),
        currency="eur", reason="Rakelsets für 12 Filialen",
    )  # fmt: skip
    history = outcome_history(engine, cid)
    assert [h["outcome"] for h in history] == ["angebot", "kontakt"]
    assert history[0]["value"] == "12500.00" and history[0]["currency"] == "EUR"
    assert current_outcome(engine, cid)["outcome"] == "angebot"  # type: ignore[index]
    for kwargs, message in (
        ({"outcome": "bestellt"}, "unbekannt"),
        ({"occurred_on": TODAY + timedelta(days=1)}, "Zukunft"),
        ({"value": "100"}, "Währung"),
        ({"value": "-5", "currency": "EUR"}, "≥ 0"),
        ({"currency": "EUR"}, "ohne Betrag"),
        ({"author": " "}, "Autor"),
    ):
        args: dict[str, Any] = {"outcome": "verloren", "occurred_on": TODAY, "author": "V"}
        args.update(kwargs)
        with pytest.raises(OutcomeError, match=message):
            record_outcome(engine, cid, args.pop("outcome"), **args)
    with pytest.raises(OutcomeError, match="nicht gefunden"):
        record_outcome(engine, 999_999, "kontakt", occurred_on=TODAY, author="V")


def test_outcomes_are_append_only(world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["glas"]
    record_outcome(engine, cid, "kontakt", occurred_on=TODAY, author="V")
    for statement in ("UPDATE sales_outcome SET outcome = 'gewonnen'", "DELETE FROM sales_outcome"):
        with pytest.raises(Exception, match="unveränderlich"), engine.begin() as conn:
            conn.execute(text(statement))
    assert len(outcome_history(engine, cid)) == 1


def test_won_company_becomes_customer_lost_stays_visible(
    world: dict[str, Any], engine: Engine
) -> None:
    _assess(engine, world)
    won, lost = world["ids"]["folierer"], world["ids"]["messebau"]
    record_outcome(
        engine, won, "gewonnen", occurred_on=TODAY, author="V", value="5000", currency="EUR"
    )
    record_outcome(engine, lost, "verloren", occurred_on=TODAY, author="V", reason="Preis")
    best = _assess(engine, world)
    assert best[won]["segment"]["key"] == "bestandskunde"
    assert "gewonnen" in excluded_reason(engine, won, ["Bestandskunden"])  # type: ignore[operator]
    assert excluded_reason(engine, lost, ["Bestandskunden"]) is None
    rows = {r["firma_id"]: r for r in lead_rows(engine)}
    assert won not in rows  # Kunde, kein neuer Lead
    assert rows[lost]["vertriebsergebnis"].startswith("verloren am")
    everything = {r["firma_id"]: r for r in lead_rows(engine, include_excluded=True)}
    assert everything[won]["vertriebsergebnis"].endswith("5000 EUR")


def test_entry_links_the_assessment_valid_at_that_time(
    world: dict[str, Any], engine: Engine
) -> None:
    _assess(engine, world)
    cid = world["ids"]["glas"]
    with engine.connect() as conn:
        before = conn.execute(
            text("SELECT max(id) FROM opportunity_score WHERE company_id = :c"), {"c": cid}
        ).scalar()
    entry_id = record_outcome(engine, cid, "kontakt", occurred_on=TODAY, author="V")
    _assess(engine, world)  # spätere Neubewertung ändert die Verknüpfung nicht
    with engine.connect() as conn:
        linked = conn.execute(
            text("SELECT assessment_id FROM sales_outcome WHERE id = :i"), {"i": entry_id}
        ).scalar()
        row = conn.execute(
            text("SELECT company_id, details FROM opportunity_score WHERE id = :i"), {"i": linked}
        ).one()
    assert linked is not None and linked <= before and row.company_id == cid
    assert json.loads(row.details)["batch"]


def test_evaluation_counts_only_recorded_data(world: dict[str, Any], engine: Engine) -> None:
    ids = world["ids"]
    empty = evaluate(engine)
    assert empty["closed"] == 0 and not empty["enough_data"]
    assert "zu wenige" in evaluation_markdown(empty)
    with engine.begin() as conn:  # nie bewertet → zählt als „ohne Bewertung vor Kontakt“
        unrated = repo.create_company(conn, "Nie bewertet GmbH", country_code="DE")
    record_outcome(engine, unrated, "verloren", occurred_on=TODAY, author="V")
    record_outcome(engine, ids["folierer"], "kontakt", occurred_on=TODAY, author="V")
    record_outcome(engine, ids["messebau"], "kontakt", occurred_on=TODAY, author="V")
    _assess(engine, world)  # Neubewertung nach dem ersten Kontakt
    record_outcome(
        engine,
        ids["folierer"],
        "gewonnen",
        occurred_on=TODAY,
        author="V",
        value="5000",
        currency="EUR",
    )
    record_outcome(engine, ids["messebau"], "verloren", occurred_on=TODAY, author="V")
    record_outcome(engine, ids["glas"], "angebot", occurred_on=TODAY, author="V")
    result = evaluate(engine, min_cases=10)
    with engine.connect() as conn:
        linked = dict(
            conn.execute(
                text("SELECT outcome, assessment_id FROM sales_outcome WHERE company_id = :c"),
                {"c": ids["folierer"]},
            ).all()
        )
    case = next(c for c in result["cases"] if c["company_id"] == ids["folierer"])
    # Bezug ist die Bewertung vor dem ersten Kontakt, nicht die beim Abschluss gültige
    assert case["baseline_assessment_id"] == linked["kontakt"] != linked["gewonnen"]
    assert (result["closed"], result["open"], result["closed_without_baseline"]) == (3, 1, 1)
    assert result["won_values"] == {"EUR": "5000"}
    rows = result["tables"]["fit_status"]
    assert sum(r["n"] for r in rows) == 2 and all(r["anteil_gewonnen"] is None for r in rows)
    text_out = evaluation_markdown(result)
    assert "unter 10 Fällen" in text_out and "leitet keine Gewichte ab" in text_out
    shares = evaluate(engine, min_cases=1)["tables"]["segment"]
    assert {r["anteil_gewonnen"] for r in shares} <= {0.0, 1.0}
    assert sum(r["gewonnen"] for r in shares) == 1 and sum(r["verloren"] for r in shares) == 1


def test_ui_records_outcome(world: dict[str, Any], engine: Engine) -> None:
    from market_intel.ui.server import make_server

    _assess(engine, world)
    server = make_server(engine, "127.0.0.1", 0, (world["cases"], world["catalog"], world["model"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    cid = world["ids"]["glas"]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def post(fields: dict[str, str]) -> int:
        data = urllib.parse.urlencode(fields).encode()
        try:
            return opener.open(
                urllib.request.Request(f"{base}/company/{cid}/outcome", data=data)
            ).status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        page = urllib.request.urlopen(f"{base}/company/{cid}/opportunity").read().decode()
        assert "Vertriebsergebnis" in page and "noch kein Ergebnis erfasst" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        fields = {
            "csrf": token, "outcome": "angebot", "occurred_on": TODAY.isoformat(),
            "value": "830", "currency": "EUR", "reason": "Muster", "author": "V",
        }  # fmt: skip
        assert post(fields) == 303
        assert post({**fields, "csrf": "falsch"}) == 403
        assert post({**fields, "occurred_on": "kein-datum"}) == 400
        page = urllib.request.urlopen(f"{base}/company/{cid}/opportunity").read().decode()
        assert "Angebot abgegeben am" in page and "830 EUR" in page
    finally:
        server.shutdown()
        server.server_close()
    assert db.SCHEMA_VERSION == 5
