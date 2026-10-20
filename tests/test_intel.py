"""Abnahme Phase 3: Profil, Rückverfolgbarkeit, Historie, Wettbewerb, Erfassung, Oberfläche."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, func, select

from market_intel import db, repo
from market_intel.intel import manual
from market_intel.intel.competitors import analyze_competitors
from market_intel.intel.profile import build_profile, observation_detail
from market_intel.ui.server import export_profile_html, make_server

from .fakeweb import PAGES, FakeWeb
from .intel_seed import seed
from .test_end_to_end import PROOF_REQUEST


def _profile(engine: Engine, company_id: int, **kw: Any) -> dict[str, Any]:
    with engine.connect() as conn:
        return build_profile(conn, company_id, as_of=date(2026, 10, 20), **kw)


def _series(profile: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    return profile[section][key][0]


# ---------------------------------------------------------------------- Berechnungen im Profil


def test_revenue_history_with_gap_and_acquisition(engine: Engine) -> None:
    ids = seed(engine)
    profile = _profile(engine, ids["muster"])
    revenue = _series(profile, "finance", "revenue")
    rows = {r["label"]: r for r in revenue["rows"]}
    assert list(rows) == ["2018", "2019", "2020", "2021", "2022", "2023"]
    assert rows["2020"]["point"] is None and rows["2020"]["status"] == "gap"
    assert rows["2021"]["change_pct"] is None and rows["2021"]["since_label"] == "2019"
    assert rows["2022"]["change_pct"] == "30.4" and "inorganic" in rows["2022"]["flags"]
    assert rows["2023"]["change_pct"] == "-5.0"  # negatives Wachstum
    assert rows["2023"]["change_abs"] == "-3300000"
    total = revenue["spans"][revenue["total_index"]]
    assert (total["start_label"], total["end_label"], total["years"]) == ("2018", "2023", 5)
    # (62,7 / 40)^(1/5) − 1 = 9,4 %
    assert total["cagr_pct"] == "9.4" and total["inorganic"]
    assert total["missing_years"] == ["2020"]
    # jede Zahl trägt Jahr, Scope und Herkunft
    point = rows["2023"]["point"]
    assert (point["scope_label"], point["provenance_label"], point["official"]) == (
        "Konzern",
        "amtlich/Pflichtveröffentlichung",
        True,
    )


def test_headcount_net_change_is_not_hiring(engine: Engine) -> None:
    ids = seed(engine)
    profile = _profile(engine, ids["muster"])
    heads = _series(profile, "employees", "series")
    dyn = {d["label"]: d for d in heads["dynamics"]}
    assert dyn["2021"]["net_change"] is None and "unbekannt" in dyn["2021"]["statement"]
    assert dyn["2023"]["net_change"] == "-10"
    assert dyn["2023"]["documented_layoffs"] == 30
    assert "Einstellungen und Abgänge nicht ableiten" in dyn["2023"]["statement"]
    # Segment nach Land getrennt, nicht mit dem Gesamtwert vermischt
    segments = profile["employees"]["segments"]
    assert segments and segments[0]["segment"] == "country:DE"
    assert [r["point"]["value"] for r in segments[0]["rows"]] == ["350"]
    assert heads["rows"][-1]["point"]["value"] == "400"


def test_every_displayed_figure_traces_to_quote_in_document(engine: Engine) -> None:
    ids = seed(engine)
    profile = _profile(engine, ids["muster"])
    points = []
    for item in profile["finance"]["revenue"] + profile["employees"]["series"]:
        points.extend(r["point"] for r in item["rows"] if r["point"])
    points.append(profile["overview"]["latest_revenue"])
    assert len(points) >= 10
    refs = {s["ref"] for s in profile["sources"]}
    with engine.connect() as conn:
        for point in points:
            assert point["source_ref"] in refs
            detail = observation_detail(conn, point["kind"], point["observation_id"])
            assert detail is not None
            obs = detail["observation"]
            assert Decimal(obs["value"]) == Decimal(point["value"])
            text = manual.document_plain_text(engine, obs["source_document_id"])
            assert obs["quote"] in text and obs["quote_verified"]


def test_profile_sections_and_unknowns(engine: Engine) -> None:
    ids = seed(engine)
    profile = _profile(engine, ids["other"])
    assert [s["key"] for s in profile["sections"]] == [
        "overview",
        "finance",
        "employees",
        "locations",
        "products",
        "markets",
        "history",
        "sources",
        "notes",
    ]
    assert profile["overview"]["latest_revenue"] is None
    assert {"Umsatz", "Mitarbeiterzahl", "Hauptsitz"} <= set(profile["overview"]["unknown"])
    muster = _profile(engine, ids["muster"])
    footprint = muster["locations"]
    assert footprint["map_points"][0]["city"] == "Leipzig"
    presence = {c["country"]: c["presence"] for c in footprint["countries"]}
    assert presence == {"DE": "physical", "FR": "export", "AT": "export"}


# ---------------------------------------------------------------------- Wettbewerb


def test_competitor_classification_without_market_shares(engine: Engine) -> None:
    ids = seed(engine)
    with engine.begin() as conn:
        result = analyze_competitors(conn, ids["muster"], store=True)
    direct = [r["name"] for r in result["groups"]["direct"]]
    assert direct == ["Rivale Wrap AG"]
    rival = result["groups"]["direct"][0]
    assert rival["dimensions"]["revenue"]["known"]
    assert rival["dimensions"]["revenue"]["ratio"] == "0.5"
    # 31,0 / 62,7 = 0,494 → Einordnung nach ungerundetem Verhältnis
    assert rival["dimensions"]["revenue"]["text"].startswith("deutlich kleiner")
    assert (
        "unterschiedlicher oder unbekannter Konsolidierungskreis"
        in rival["dimensions"]["revenue"]["text"]
    )
    others = {r["name"]: k for k, items in result["groups"].items() for r in items}
    assert others["Andere Branche GmbH"] == "no_relation"
    assert others["Beta Tape AG"] == "insufficient_data"
    assert "Keine Marktanteile" in result["note"]
    assert not any(
        "anteil" in json.dumps(r["dimensions"]).lower() for r in result["groups"]["direct"]
    )
    profile = _profile(engine, ids["muster"])
    assert profile["overview"]["competitors"][0]["name"] == "Rivale Wrap AG"


# ---------------------------------------------------------------------- Erfassung


def test_manual_entry_rejects_unbacked_values(engine: Engine) -> None:
    ids = seed(engine)
    with pytest.raises(manual.ManualEntryError, match="nicht wörtlich"):
        manual.add_figure(
            engine,
            company_id=ids["muster"],
            document_id=ids["report"],
            quote="Umsatz 2024: 99.000.000 EUR.",
            locator="",
            metric="revenue",
            value=Decimal(99_000_000),
            currency="EUR",
            period_end=date(2024, 12, 31),
        )
    with pytest.raises(manual.ManualEntryError, match="steht nicht im Zitat"):
        manual.add_figure(
            engine,
            company_id=ids["muster"],
            document_id=ids["report"],
            quote="Umsatzerlöse Konzern 2023: 62.700.000 EUR.",
            locator="",
            metric="revenue",
            value=Decimal(70_000_000),
            currency="EUR",
            period_end=date(2023, 12, 31),
        )
    with pytest.raises(manual.ManualEntryError, match="Methodik"):
        manual.add_figure(
            engine,
            company_id=ids["muster"],
            document_id=ids["report"],
            quote="Mitarbeiter zum 31.12.2023: 400 Personen.",
            locator="",
            metric="revenue",
            value=Decimal(80_000_000),
            currency="EUR",
            period_end=date(2024, 12, 31),
            provenance="estimate",
        )
    with pytest.raises(manual.ManualEntryError, match="Herkunftsangabe"):
        manual.add_location(
            engine,
            company_id=ids["muster"],
            document_id=ids["report"],
            quote="Unser Werk in Leipzig fertigt Car-Wrapping-Folien.",
            locator="",
            location_type="production",
            country="DE",
            city="Leipzig",
            latitude=Decimal(1),
            longitude=Decimal(1),
            geo_precision="site",
        )
    estimate = manual.add_figure(
        engine,
        company_id=ids["muster"],
        document_id=ids["report"],
        quote="Mitarbeiter zum 31.12.2023: 400 Personen.",
        locator="",
        metric="revenue",
        value=Decimal(80_000_000),
        currency="EUR",
        period_end=date(2024, 12, 31),
        provenance="estimate",
        methodology="400 Mitarbeiter × 200.000 EUR Branchenumsatz je Mitarbeiter (Annahme)",
        limitations="Unsicherheit ±30 %",
    )
    assert estimate.created
    plain = _series(_profile(engine, ids["muster"]), "finance", "revenue")
    assert plain["rows"][-1]["label"] == "2023"  # Schätzung standardmäßig nicht einbezogen
    assert any("Schätzung" in x["reason"] for x in plain["excluded"])
    with_est = _series(
        _profile(engine, ids["muster"], include_estimates=True), "finance", "revenue"
    )
    assert with_est["rows"][-1]["flags"] == ["estimate"]


def test_currency_change_shows_break_in_profile(engine: Engine) -> None:
    ids = seed(engine)
    text = "Umsatz 2021: 10.000.000 USD. Umsatz 2022: 11.000.000 USD. Umsatz 2023: 10.500.000 EUR."
    from market_intel.sources.user_data import store_document

    document_id = store_document(
        engine,
        "user_upload",
        text.encode(),
        url="upload:fx.txt",
        title="fx",
        publisher=None,
        published=None,
        content_type="text/plain",
    )
    for year, value, cur in (
        (2021, 10_000_000, "USD"),
        (2022, 11_000_000, "USD"),
        (2023, 10_500_000, "EUR"),
    ):
        manual.add_figure(
            engine,
            company_id=ids["other"],
            document_id=document_id,
            quote=f"Umsatz {year}: {value:,} {cur}.".replace(",", "."),
            locator="",
            metric="revenue",
            value=Decimal(value),
            currency=cur,
            scope="group",
            period_end=date(year, 12, 31),
        )
    series = _profile(engine, ids["other"])["finance"]["revenue"]
    usd = next(s for s in series if s["unit"] == "USD")
    eur = next(s for s in series if s["unit"] == "EUR")
    assert [r["change_pct"] for r in usd["rows"]] == [None, "10.0"]
    assert len(eur["rows"]) == 1 and eur["total_index"] is None  # kein Vergleich über Währungen


# ---------------------------------------------------------------------- Historie


def test_history_preserved_and_changes_detected_on_rerun(
    run_research: Any, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_research(PROOF_REQUEST)
    with engine.connect() as conn:
        orafol = repo.find_company_by_identifier(conn, "lei", "TESTLEIORAFOL0000001")
        assert orafol is not None
        before = {
            r["id"]: r["value"]
            for r in repo.observations(conn, orafol, "employee", "headcount_total")
        }
    pages = dict(PAGES)
    pages["https://www.orafol-test.test/presse"] = (
        "<html><head><title>Presse</title></head><body>"
        "<p>12.05.2026: ORAFOL eröffnet ein neues Werk in Polen und erweitert die Produktion.</p>"
        "<p>01.09.2026: ORAFOL übernimmt die Beispiel Druck GmbH in Wien.</p>"
        "<p>Unser neues Logistikzentrum in Tschechien beliefert Osteuropa.</p>"
        "<p>Heute arbeiten 3.100 Mitarbeiter für ORAFOL.</p></body></html>"
    )
    monkeypatch.setattr("tests.fakeweb.PAGES", pages)
    with engine.begin() as conn:  # einige Wochen später: Zwischenspeicher abgelaufen
        conn.execute(db.http_cache.update().values(expires_at=datetime(2000, 1, 1, tzinfo=UTC)))
    _, run2, status = run_research(dataclasses.replace(PROOF_REQUEST, refresh=True))
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        after = {
            r["id"]: r["value"]
            for r in repo.observations(conn, orafol, "employee", "headcount_total")
        }
        assert set(before) <= set(after)  # nichts gelöscht oder überschrieben
        assert all(after[k] == v for k, v in before.items())
        changes = (
            conn.execute(
                select(db.profile_change).where(
                    db.profile_change.c.company_id == orafol,
                    db.profile_change.c.research_run_id == run2,
                )
            )
            .mappings()
            .all()
        )
    kinds = {c["change_kind"] for c in changes}
    assert "event:acquisition" in kinds and "new_location" in kinds
    acquisition = next(c for c in changes if c["change_kind"] == "event:acquisition")
    assert acquisition["event_date"] == date(2026, 9, 1)  # Datum laut Quelle
    assert acquisition["detected_at"].date() >= acquisition["event_date"]  # erfahren danach
    assert acquisition["evidence_id"] is not None and acquisition["source_document_id"]
    profile = _profile(engine, orafol)
    timeline = profile["history"]["timeline"]
    assert any(
        t["kind"] == "event:acquisition" and t["event_date"] == "2026-09-01" for t in timeline
    )
    heads = [
        t for t in timeline if t.get("metric") == "headcount_total" and t["kind"] == "new_period"
    ]
    assert heads and "Nettoveränderung, keine Zahl von Einstellungen" in heads[0]["summary"]
    assert heads[0]["summary"].startswith("Mitarbeiter (Stichtag)")
    # unbestätigte Pressemeldung bricht die Reihe nicht
    for item in profile["finance"]["revenue"]:
        assert all("inorganic" not in r["flags"] for r in item["rows"])


def test_first_research_creates_no_profile_changes(run_research: Any, engine: Engine) -> None:
    run_research(PROOF_REQUEST)
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(db.profile_change)).scalar()
    assert count == 0  # Ersterfassung ist keine Veränderung


# ---------------------------------------------------------------------- Oberfläche


@pytest.fixture
def server(engine: Engine) -> Iterator[tuple[str, dict[str, int]]]:
    ids = seed(engine)
    srv = make_server(engine, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", ids
    srv.shutdown()
    srv.server_close()


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def _post(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> int:
    body = urllib.parse.urlencode(data).encode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with opener.open(request) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def test_ui_profile_page_sections_security_and_gaps(server: tuple[str, dict[str, int]]) -> None:
    base, ids = server
    status, headers, page = _get(f"{base}/company/{ids['muster']}")
    assert status == 200
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    for label in (
        "Überblick",
        "Finanzen",
        "Mitarbeiter",
        "Standorte",
        "Produkte",
        "Märkte",
        "Historie",
        "Quellen",
        "Interne Notizen",
    ):
        assert f">{label}</a>" in page
    assert "<script>" not in page  # keine Inline-Skripte
    gap_row = re.search(
        r'<tr class="st-gap"><th scope="row">2020</th><td class="num">(.*?)</td>', page
    )
    assert gap_row and "unbekannt" in gap_row.group(1)  # Lücke nie als 0
    assert "CAGR 9,4 %" in page and "nicht organisch" in page
    data = json.loads(re.search(r'id="profile-data">(.*?)</script>', page, re.S).group(1))  # type: ignore[union-attr]
    assert data["company_id"] == ids["muster"]
    status, _, detail = _get(f"{base}/observation/financial/1")
    assert status == 200 and "Umsatzerlöse Konzern 2018" in detail
    status, _, _ = _get(f"{base}/company/{ids['muster']}", {"Host": "evil.test"})
    assert status == 421  # DNS-Rebinding-Schutz
    status, _, competitors = _get(f"{base}/company/{ids['muster']}/competitors")
    assert (
        status == 200
        and "direkter Wettbewerber" in competitors
        and "Keine Marktanteile" in competitors
    )


def test_ui_notes_require_csrf_and_keep_versions(
    server: tuple[str, dict[str, int]], engine: Engine
) -> None:
    base, ids = server
    url = f"{base}/company/{ids['muster']}/notes"
    assert _post(url, {"section": "finance", "author": "A", "body": "x"}) == 403
    _, _, page = _get(f"{base}/company/{ids['muster']}")
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
    assert (
        _post(
            url,
            {"csrf": token, "section": "finance", "author": "A", "body": "x"},
            {"Origin": "https://evil.test"},
        )
        == 403
    )
    assert (
        _post(
            url,
            {
                "csrf": token,
                "section": "finance",
                "author": "Vertrieb",
                "body": "<b>Termin</b> im Mai",
            },
        )
        == 303
    )
    _, _, page = _get(f"{base}/company/{ids['muster']}")
    assert "&lt;b&gt;Termin&lt;/b&gt; im Mai" in page and "<b>Termin</b>" not in page
    with engine.connect() as conn:
        note_id = repo.notes_of(conn, ids["muster"])[0]["id"]
    assert _post(f"{url}/{note_id}/delete", {"csrf": token}) == 303
    with engine.connect() as conn:
        assert repo.notes_of(conn, ids["muster"]) == []
        assert len(repo.notes_of(conn, ids["muster"], history=True)) == 2


def test_static_export_is_self_contained(engine: Engine) -> None:
    ids = seed(engine)
    html = export_profile_html(engine, ids["muster"])
    assert "<style>" in html and 'id="world-map"' in html
    assert not re.search(r'(src|href)="https?://', html)  # keine externen Ressourcen
    assert "Notiz speichern" not in html  # Export ist nur lesend


# ---------------------------------------------------------------------- Nachbesserungen


def test_headcount_kind_and_group_scope_from_text(make_ctx: Any, engine: Engine) -> None:
    from market_intel.engine.extract import extract_company_documents, headcount_metric, scope_in
    from market_intel.sources.user_data import store_document

    assert headcount_metric("Im Jahresdurchschnitt 2023 waren 2.950 Mitarbeiter tätig.") == (
        "headcount_avg"
    )
    assert headcount_metric("2023 entsprach dies 2.400 Vollzeitäquivalenten (FTE).") == (
        "headcount_fte"
    )
    assert headcount_metric("Zum 31.12.2023 beschäftigten wir 3.010 Mitarbeiter.") == (
        "headcount_total"
    )
    assert scope_in("Der Konzernumsatz 2023 betrug 812,5 Mio. EUR.").value == "group"
    assert scope_in("Der Umsatz 2023 betrug 812,5 Mio. EUR.").value == "unknown"

    ctx = make_ctx(FakeWeb())
    with engine.begin() as conn:
        company_id = repo.create_company(conn, "Text GmbH")
    text = (
        "Lagebericht 2023. Im Geschäftsjahr 2023 lag der Konzernumsatz bei 120,0 Mio. EUR. "
        "Im Jahresdurchschnitt 2023 waren 950 Mitarbeiter beschäftigt. "
        "Zum 31.12.2023 beschäftigte der Konzern 1.010 Mitarbeiter."
    )
    document_id = store_document(
        engine,
        "user_upload",
        text.encode(),
        url="upload:lb.txt",
        title="lb",
        publisher=None,
        published=None,
        content_type="text/plain",
    )
    extract_company_documents(ctx, company_id, [document_id], {}, [])
    with engine.connect() as conn:
        heads = {
            (r["metric"], r["value"], r["consolidation_scope"], r["period_type"])
            for r in repo.observations(conn, company_id, "employee")
        }
        revenue = repo.observations(conn, company_id, "financial", "revenue")
    assert ("headcount_avg", 950, "unknown", "fiscal_year") in heads
    assert ("headcount_total", 1010, "group", "point_in_time") in heads
    assert not any(m == "headcount_total" and v == 950 for m, v, _, _ in heads)  # nicht vermischt
    assert [(r["value"], r["consolidation_scope"]) for r in revenue] == [(120_000_000, "group")]


def test_report_and_profile_use_same_growth_rules(engine: Engine) -> None:
    from market_intel.analytics import compare

    ids = seed(engine)
    with engine.connect() as conn:
        row = compare(conn, [ids["muster"]])[0]
    profile = _profile(engine, ids["muster"])
    total = _series(profile, "finance", "revenue")
    span = total["spans"][total["total_index"]]
    assert str(row["revenue_cagr_pct"]) == span["cagr_pct"] == "9.4"
    assert "kein organisches Wachstum" in row["revenue_note"]
    assert "Zwischenjahre unbekannt" in row["revenue_note"]
