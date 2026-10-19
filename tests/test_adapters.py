"""Quellenadapter einzeln: korrekte, fehlerhafte und unvollständige Antworten (Mock-Daten)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from market_intel import db, repo
from market_intel.engine.adapters import (
    ADAPTERS,
    CompaniesHouseAdapter,
    EurostatAdapter,
    Kind,
    adapter_overview,
    resolve_named,
)
from market_intel.engine.strategy import ResearchRequest, build_strategy
from market_intel.sources import eurostat, gleif, sec_edgar, ted, wikidata
from market_intel.sources.user_data import import_company_list

from .fakeweb import FakeWeb

Route = Callable[[httpx.Request], httpx.Response]


class PatchedWeb(FakeWeb):
    """FakeWeb mit ersetzten Antworten je Host."""

    def __init__(self, routes: dict[str, Route]) -> None:
        super().__init__()
        self.routes = routes

    def handle(self, request: httpx.Request) -> httpx.Response:
        route = self.routes.get(request.url.host)
        if route is not None:
            self.calls[request.url.host] += 1
            return route(request)
        return super().handle(request)


BROKEN_BODIES = [
    httpx.Response(
        200,
        text="<html><body>Wartungsarbeiten</body></html>",
        headers={"content-type": "text/html"},
    ),
    httpx.Response(200, text="{kaputt"),
    httpx.Response(200, json=["liste", "statt", "objekt"]),
    httpx.Response(
        200,
        json={
            "data": "kein array",
            "notices": {"x": 1},
            "search": "x",
            "entities": [],
            "results": {"bindings": "x"},
            "facts": [],
        },
    ),
    httpx.Response(
        200,
        json={"data": [{"attributes": {"entity": {"legalName": "falscher Typ"}}}, "string", None]},
    ),
]


@pytest.mark.parametrize("body", BROKEN_BODIES)
@pytest.mark.parametrize(
    "host", ["api.gleif.org", "www.wikidata.org", "api.ted.europa.eu", "data.sec.gov"]
)
def test_broken_responses_yield_no_data_and_no_crash(make_ctx, engine, host, body):
    ctx = make_ctx(PatchedWeb({host: lambda r, b=body: b}))
    with engine.begin() as conn:
        company_id = repo.create_company(conn, "X GmbH", country_code="DE")
    if host == "api.gleif.org":
        assert gleif.search(ctx, "X", country="DE")[0] == []
        assert gleif.fetch_by_lei(ctx, "TESTLEI")[0] is None
    elif host == "www.wikidata.org":
        assert wikidata.search_entities(ctx, "X") == []
        assert wikidata.ingest_entity(ctx, "Q1", company_id=company_id) in (None, company_id)
    elif host == "api.ted.europa.eu":
        assert ted.search(ctx, "Folierung", "DE")[0] == []
    else:
        assert sec_edgar.ingest_facts(ctx, company_id, 1) == 0
    with engine.connect() as conn:
        assert repo.observations(conn, company_id, "financial") == []


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_http_errors_are_reported_per_source(make_ctx, sleeps, status):
    ctx = make_ctx(PatchedWeb({"api.gleif.org": lambda r: httpx.Response(status)}))
    records, doc = gleif.search(ctx, "X")
    assert records == [] and doc is None
    assert any("GLEIF-Suche" in n for n in ctx.notes)


def test_incomplete_gleif_record_is_skipped():
    assert gleif.parse_record({"attributes": {"lei": "L", "entity": {}}}) is None
    record = gleif.parse_record(
        {"id": "L1", "attributes": {"entity": {"legalName": {"name": "Minimal GmbH"}}}}
    )
    assert record is not None and record.city is None and record.country is None


def test_wikidata_values_without_point_in_time_are_not_stored(make_ctx, engine):
    entity = {
        "id": "Q5",
        "labels": {"de": {"value": "Ohne Datum AG"}},
        "claims": {
            "P1128": [
                {
                    "rank": "normal",
                    "mainsnak": {"datavalue": {"value": {"amount": "+500", "unit": "1"}}},
                }
            ],
            "P2139": [
                {
                    "rank": "deprecated",
                    "mainsnak": {"datavalue": {"value": {"amount": "+9", "unit": "x"}}},
                }
            ],
        },
    }
    body = json.dumps({"entities": {"Q5": entity}}, separators=(",", ":"))
    ctx = make_ctx(PatchedWeb({"www.wikidata.org": lambda r: httpx.Response(200, text=body)}))
    company_id = wikidata.ingest_entity(ctx, "Q5")
    with engine.connect() as conn:
        assert repo.observations(conn, company_id, "employee") == []


def test_sec_ignores_quarters_and_wrong_lengths(make_ctx, engine):
    entries = [
        {"start": "2023-01-01", "end": "2023-03-31", "val": 1, "fp": "Q1", "form": "10-Q"},
        {
            "start": "2023-01-01",
            "end": "2023-12-31",
            "val": 100,
            "fp": "FY",
            "form": "10-K",
            "filed": "2024-02-01",
            "accn": "a",
        },
        {
            "start": "2023-01-01",
            "end": "2023-12-31",
            "val": 101,
            "fp": "FY",
            "form": "10-K/A",
            "filed": "2024-05-01",
            "accn": "b",
        },
        {"start": "2022-06-01", "end": "2023-12-31", "val": 7, "fp": "FY", "form": "10-K"},
        {"start": "kaputt", "end": "2023-12-31", "val": 7, "fp": "FY", "form": "10-K"},
    ]
    body = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}
    ctx = make_ctx(PatchedWeb({"data.sec.gov": lambda r: httpx.Response(200, json=body)}))
    with engine.begin() as conn:
        company_id = repo.create_company(conn, "Y Inc", country_code="US")
    assert sec_edgar.ingest_facts(ctx, company_id, 5) == 1
    with engine.connect() as conn:
        rows = repo.observations(conn, company_id, "financial", "revenue")
    assert [(r["value"], r["limitations"]) for r in rows] == [
        (101, "Formular 10-K/A, eingereicht 2024-05-01")
    ]  # spätere Korrektur gewinnt


def test_gleif_parents_404_is_reporting_exception_not_error(make_ctx, engine, cases):
    ctx = make_ctx(FakeWeb())
    company_id, _notes = resolve_named(ctx, "ORAFOL", ["DE"])
    assert company_id is not None
    gleif.enrich_parents(ctx, company_id, "TESTLEIORAFOL0000001")
    assert any("keine Mutter gemeldet" in n for n in ctx.notes)


def test_companies_house_requires_free_key_and_parses(make_ctx, engine, monkeypatch, cases):
    adapter = CompaniesHouseAdapter()
    assert not adapter.enabled(make_ctx(FakeWeb()))  # ohne Schlüssel aus
    monkeypatch.setenv("MI_COMPANIES_HOUSE_KEY", "test-key")
    seen = {}

    def route(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        if request.url.path == "/search/companies":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "company_number": "01234567",
                            "title": "WRAP TOOLS LTD",
                            "company_status": "active",
                            "address": {"locality": "Leeds"},
                        },
                        {"title": "ohne Nummer"},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "company_number": "01234567",
                "company_name": "WRAP TOOLS LTD",
                "company_status": "active",
                "sic_codes": ["25730"],
                "registered_office_address": {"locality": "Leeds"},
            },
        )

    ctx = make_ctx(PatchedWeb({"api.company-information.service.gov.uk": route}))
    assert adapter.enabled(ctx)
    strategy = build_strategy(
        ResearchRequest(task="Werkzeug", countries=["GB"], industries=["Tools"]), cases
    )
    found = adapter.find_candidates(ctx, strategy, cases)
    assert len(found) == 1 and seen["auth"].startswith("Basic ")
    outcome = adapter.investigate(ctx, found[0].company_id, strategy, cases)
    assert outcome.status == "ok" and "25730" in outcome.detail
    with engine.connect() as conn:
        assert repo.identifiers_of(conn, found[0].company_id)["uk_crn"] == "01234567"


def test_eurostat_jsonstat_parsing_and_storage(make_ctx, engine, cases):
    body = {
        "version": "2.0",
        "class": "dataset",
        "id": ["indic_sbs", "geo", "time"],
        "size": [2, 1, 2],
        "dimension": {
            "indic_sbs": {
                "category": {
                    "index": {"ENT": 0, "EMP": 1},
                    "label": {"ENT": "Unternehmen", "EMP": "Beschäftigte"},
                }
            },
            "geo": {"category": {"index": {"DE": 0}}},
            "time": {"category": {"index": {"2022": 0, "2023": 1}}},
        },
        "value": {"0": 1200, "1": 1210, "3": 15300},
        "status": {"3": "p"},
    }
    cells = eurostat.parse_jsonstat(body)
    assert [(c.dimensions["indic_sbs"], c.period, c.value, c.status) for c in cells] == [
        ("ENT Unternehmen", "2022", 1200, None),
        ("ENT Unternehmen", "2023", 1210, None),
        ("EMP Beschäftigte", "2023", 15300, "p"),
    ]
    with pytest.raises(ValueError):
        eurostat.parse_jsonstat({"id": ["a"], "size": [2], "dimension": {}, "value": {}})
    ctx = make_ctx(PatchedWeb({"ec.europa.eu": lambda r: httpx.Response(200, json=body)}))
    strategy = build_strategy(ResearchRequest(task="Beschriftungswerkzeuge Deutschland"), cases)
    assert strategy.nace == ["25.73"]
    EurostatAdapter().find_candidates(ctx, strategy, cases)
    with engine.connect() as conn:
        assert len(conn.execute(select(db.market_statistic)).all()) == 3


def test_user_list_import_matches_by_domain(engine, tmp_path, cases):
    from market_intel.repo import sync_sources

    sync_sources(engine, (), (), False)
    with engine.begin() as conn:
        existing = repo.create_company(conn, "Beta Werbetechnik", website="https://beta.test/")
        repo.add_identifier(conn, existing, "domain", "beta.test", None)
    csv_file = tmp_path / "aussteller.csv"
    csv_file.write_text(
        "name;country;website\nBeta Werbetechnik GmbH;DE;www.beta.test\n"
        ";DE;\nNeu Folien GmbH;DE;\n",
        encoding="utf-8",
    )
    result = import_company_list(
        engine, csv_file, list_name="Messe 2026", source_url="", publisher=None
    )
    assert result.skipped_rows == 1 and existing in result.companies
    assert len(result.companies) == 2


def test_every_requirement_kind_is_documented():
    covered = {k for a in ADAPTERS for k in a.kinds}
    missing = set(Kind) - covered
    assert missing == {Kind.WEB_SEARCH, Kind.LICENSED}  # bewusst ohne Adapter, siehe Doku
    assert {row["adapter"] for row in adapter_overview()} == {a.key for a in ADAPTERS}
