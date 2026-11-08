# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
"""Phase D: TED-Rollen, Datenpflege (Websites, Identitäten), Import mit Prüfbericht."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from market_intel import db, repo
from market_intel.engine.strategy import ResearchRequest
from market_intel.maintenance import (
    MaintenanceError,
    company_group,
    duplicate_groups,
    edits_of,
    mark_distinct,
    merge,
    missing_websites,
    set_field,
    set_website,
)
from market_intel.opportunity.current import freshness
from market_intel.opportunity.evidence import company_documents
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.model import load_model
from market_intel.products import load_products
from market_intel.sources import ted
from market_intel.sources.user_data import import_company_bytes

from .fakeweb import FakeWeb  # noqa: F401
from .test_opportunity import world  # noqa: F401  (Fixture)

TED_REQUEST = ResearchRequest(
    task="Finde Fahrzeugfolierer", countries=["DE"], use_cases=["fahrzeugfolierung"]
)


@pytest.fixture(autouse=True)
def _sources(engine: Engine) -> None:
    repo.sync_sources(engine, (), (), False)  # wie beim Start von mi ui


def _company(engine: Engine, name: str, **kw: Any) -> int:
    with engine.begin() as conn:
        return repo.create_company(conn, name, **kw)


# ---------------------------------------------------------------------- TED


def test_ted_parses_winners_and_only_assigns_vat_for_single_winner() -> None:
    single = ted.parse_notice(
        {
            "publication-number": "1-2026",
            "notice-title": {"deu": "Fahrzeugbeschriftung"},
            "notice-type": "can-standard",
            "winner-name": {"deu": ["A GmbH"]},
            "winner-country": ["DEU"],
            "winner-identifier": ["DE 811 125 281"],
        }
    )
    assert single is not None and single.is_award
    assert single.winners == (("A GmbH", "DEU", "DE811125281"),)
    several = ted.parse_notice(
        {
            "publication-number": "2-2026",
            "notice-title": {"deu": "Lose"},
            "notice-type": "can-standard",
            "winner-name": {"deu": ["A GmbH", "B GmbH"]},
            "winner-country": ["DEU", "DEU"],
            "winner-identifier": ["DE1", "DE2"],
        }
    )
    assert several is not None
    # Reihenfolge der Listen ist laut Live-Abruf nicht positionsgleich → keine Zuordnung
    assert several.winners == (("A GmbH", None, None), ("B GmbH", None, None))
    contract = ted.parse_notice(
        {"publication-number": "3-2026", "notice-title": {"deu": "x"}, "notice-type": "cn-standard"}
    )
    assert contract is not None and not contract.is_award and contract.winners == ()


def test_ted_buyers_are_signals_winners_are_candidates(run_research: Any, engine: Engine) -> None:
    _ctx, run_id, status = run_research(TED_REQUEST)
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        by_name = {
            r.display_name: r
            for r in conn.execute(
                select(db.company.c.id, db.company.c.display_name, db.company.c.entity_kind)
            )
        }
        candidates = {
            r.company_id: r.selected
            for r in conn.execute(
                select(db.run_candidate.c.company_id, db.run_candidate.c.selected).where(
                    db.run_candidate.c.run_id == run_id, db.run_candidate.c.adapter_key == "ted"
                )
            )
        }
        signals = {
            (r.company_id, r.signal_type)
            for r in conn.execute(
                select(db.business_signal.c.company_id, db.business_signal.c.signal_type)
            )
        }
        vat = conn.execute(
            select(db.company_identifier.c.value).where(
                db.company_identifier.c.scheme == "vat",
                db.company_identifier.c.company_id == by_name["Folien Zuschlag GmbH"].id,
            )
        ).scalar()
    for buyer in ("Stadtwerke Teststadt", "Landkreis Testkreis", "Stadtverwaltung Testort"):
        assert by_name[buyer].entity_kind == "public_buyer"
        assert by_name[buyer].id not in candidates  # nie Kandidat
        assert (by_name[buyer].id, "tender_published") in signals  # aber Bedarfssignal
    winner = by_name["Folien Zuschlag GmbH"]
    assert winner.entity_kind == "legal_entity" and candidates[winner.id] is True
    assert (winner.id, "tender_awarded") in signals
    assert vat == "DE987654321"
    # Gewinner einer Bekanntmachung, deren Titel den Begriff nicht nennt: nicht übernommen
    assert "Fahrzeugbau Groß AG" not in by_name and "Folien Los3 GmbH" not in by_name


def test_public_buyers_never_selected_and_hidden_from_leads(
    run_research: Any, engine: Engine
) -> None:
    from market_intel.opportunity.run import assess_and_store
    from market_intel.usecases import load_use_cases

    _ctx, run_id, _ = run_research(TED_REQUEST)
    with engine.begin() as conn:  # Altbestand: Auftraggeber war früher Kandidat
        buyer = conn.execute(
            select(db.company.c.id).where(db.company.c.display_name == "Stadtwerke Teststadt")
        ).scalar()
    assess_and_store(engine, [buyer], load_use_cases(), load_products(), load_model())
    assert all(r["firma_id"] != buyer for r in lead_rows(engine, include_no_fit=True))
    assert any(
        r["firma_id"] == buyer
        for r in lead_rows(engine, include_no_fit=True, include_excluded=True)
    )
    with engine.connect() as conn:
        summary = json.loads(
            conn.execute(
                select(db.research_task.c.result).where(
                    db.research_task.c.run_id == run_id, db.research_task.c.task_type == "select"
                )
            ).scalar()
        )
    assert buyer not in summary["selected"]


# ---------------------------------------------------------------------- Datenpflege


def test_set_website_validates_logs_and_refuses_duplicates(engine: Engine) -> None:
    a = _company(engine, "Alpha Folien GmbH", country_code="DE")
    b = _company(engine, "Beta GmbH", country_code="DE", website="beta.de")
    with pytest.raises(MaintenanceError, match="Autor"):
        set_website(engine, a, "alpha.de", " ")
    with pytest.raises(MaintenanceError, match="keine gültige"):
        set_website(engine, a, "nur text", "Vertrieb")
    with pytest.raises(MaintenanceError, match="gehört bereits zu Firma"):
        set_website(engine, a, "https://www.beta.de/kontakt", "Vertrieb")
    assert set_website(engine, a, "https://www.alpha-folien.de/impressum", "Vertrieb", "Anruf") == (
        "alpha-folien.de"
    )
    with engine.connect() as conn:
        assert repo.company_row(conn, a)["website_domain"] == "alpha-folien.de"
        assert repo.identifiers_of(conn, a)["domain"] == "alpha-folien.de"
    assert set_website(engine, a, "", "Vertrieb", "falsche Firma") == ""  # entfernen
    log = edits_of(engine, a)
    assert [(e["old_value"], e["new_value"]) for e in log] == [
        ("alpha-folien.de", None),
        (None, "alpha-folien.de"),
    ]
    assert log[0]["reason"] == "falsche Firma" and log[1]["author"] == "Vertrieb"
    with engine.begin() as conn, pytest.raises(Exception, match="unveränderlich"):
        conn.execute(text("DELETE FROM company_edit"))
    assert b  # unverändert


def test_set_field_validates(engine: Engine) -> None:
    cid = _company(engine, "Gamma GmbH")
    with pytest.raises(MaintenanceError, match="ISO"):
        set_field(engine, cid, "country_code", "Deutschland", "V")
    set_field(engine, cid, "country_code", "at", "V")
    set_field(engine, cid, "display_name", "Gamma Folientechnik GmbH", "V")
    with pytest.raises(MaintenanceError):
        set_field(engine, cid, "status", "x", "V")
    with engine.connect() as conn:
        row = repo.company_row(conn, cid)
    assert row["country_code"] == "AT" and row["display_name"] == "Gamma Folientechnik GmbH"


def test_duplicates_merge_and_distinct(engine: Engine) -> None:
    a = _company(engine, "Delta Werbetechnik GmbH", country_code="DE", website="delta.de")
    b = _company(engine, "Delta Werbetechnik GmbH", country_code="DE")
    c = _company(engine, "Epsilon AG", country_code="DE", website="eps.de")
    d = _company(engine, "Epsilon", country_code="DE", website="eps.de")
    groups = duplicate_groups(engine)
    assert {tuple(sorted(x["id"] for x in g["companies"])) for g in groups} >= {(a, b), (c, d)}
    with pytest.raises(MaintenanceError, match="begründen"):
        merge(engine, a, b, "V", "")
    merge(engine, a, b, "V", "gleiche Firma laut Handelsregister")
    mark_distinct(engine, c, d, "V", "Schwesterfirmen")
    assert duplicate_groups(engine) == []
    with engine.connect() as conn:
        assert company_group(conn, a) == [a, b]
        assert repo.company_row(conn, b)["merged_into_id"] == a
    with pytest.raises(MaintenanceError, match="zusammengeführt"):
        set_website(engine, b, "x.de", "V")
    assert any(e["field"] == "merged" for e in edits_of(engine, a))


def test_merged_records_feed_evidence_and_staleness(world: Any, engine: Engine) -> None:
    keep = world["ids"]["folien_klein"]
    other = world["ids"]["glas"]
    before = {d.document_id for d in company_documents(engine, keep)}
    ctx = (world["cases"], world["catalog"], world["model"])
    assert freshness(engine, *ctx)[keep].current
    merge(engine, keep, other, "V", "Test")
    after = {d.document_id for d in company_documents(engine, keep)}
    assert after > before  # Belege der zusammengeführten Firma zählen mit
    fresh = freshness(engine, *ctx)
    assert fresh[keep].status == "daten" and other not in fresh  # neu zu bewerten
    single = freshness(engine, *ctx, company_ids=[keep])[keep]
    assert single.status == fresh[keep].status  # gleicher Fingerabdruck einzeln wie gesamt


def test_missing_websites_excludes_public_buyers(engine: Engine) -> None:
    _company(engine, "Ohne Web GmbH")
    _company(engine, "Mit Web GmbH", website="mitweb.de")
    _company(engine, "Stadt Testheim", entity_kind="public_buyer")
    assert [c["display_name"] for c in missing_websites(engine)] == ["Ohne Web GmbH"]


# ---------------------------------------------------------------------- Import


CSV = (
    "name;website;country;lei\n"
    "Folierwerk Nord GmbH;https://www.folierwerk-nord.de;DE;\n"
    ";;DE;\n"
    "Folierwerk Nord GmbH;folierwerk-nord.de;DE;\n"
    "Wrap Süd GmbH;kein web;Bayern;\n"
    "Wrap Süd GmbH;;;\n"
    "Bekannt AG;bekannt.de;DE;\n"
).encode()


def test_import_reports_every_row_and_reimport_creates_no_duplicates(engine: Engine) -> None:
    known = _company(engine, "Bekannt AG", country_code="DE", website="bekannt.de")
    result = import_company_bytes(
        engine, CSV, filename="liste.csv", list_name="Messe 2026", source_url="", publisher=None
    )
    rows = {r.line: r for r in result.rows}
    assert rows[2].status == "neu"
    assert rows[3].status == "übersprungen" and rows[3].detail == "kein Firmenname"
    assert rows[4].status == "doppelt" and "Zeile 2" in rows[4].detail
    assert rows[5].status == "neu" and "Website" in rows[5].detail and "Land" in rows[5].detail
    assert rows[6].status == "doppelt" and "Zeile 5" in rows[6].detail
    assert rows[7].status == "zugeordnet" and rows[7].company_id == known
    assert result.skipped_rows == 1 and len(result.companies) == 3
    again = import_company_bytes(
        engine, CSV, filename="liste.csv", list_name="Messe 2026", source_url="", publisher=None
    )
    assert again.count("neu") == 0  # erneuter Import legt keine Dubletten an
    assert set(again.companies) == set(result.companies)
    with pytest.raises(ValueError, match="Spalte „name“"):
        import_company_bytes(
            engine, b"firmenname;x\nA;1\n", filename="x.csv", list_name="X", source_url="",
            publisher=None,
        )  # fmt: skip


# ---------------------------------------------------------------------- Oberfläche


@pytest.fixture
def ui(engine: Engine, cases: dict[str, Any]) -> Any:
    from market_intel.ui.server import make_server

    server = make_server(engine, "127.0.0.1", 0, (cases, load_products(), load_model()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_browser_import_maintenance_and_edit(ui: str, engine: Engine, tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    known = _company(engine, "Bekannt AG", country_code="DE", website="bekannt.de")
    dup = _company(engine, "Bekannt AG", country_code="DE")
    csv_path = tmp_path / "liste.csv"
    csv_path.write_bytes(CSV)
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium nicht installiert: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Import mit Prüfbericht
        page.goto(f"{ui}/imports")
        page.set_input_files("#file", str(csv_path))
        page.fill("#list_name", "Messe 2026")
        with page.expect_navigation():
            page.click("button:has-text('Importieren')")
        result = page.text_content("#import-result") or ""
        assert "fehlerhafte Zeilen" in result and "kein Firmenname" in result
        assert "wie Zeile 2" in result
        assert page.locator("text=Recherche für diese Liste starten").count() == 1
        # Datenpflege: Website eintragen
        page.goto(f"{ui}/maintenance?q=Wrap")
        web_form = page.locator("form[action$='/edit']").first
        web_form.locator("input[name=value]").fill("wrap-sued.de")
        web_form.locator("input[name=author]").fill("Vertrieb")
        with page.expect_navigation():
            web_form.locator("button").click()
        with engine.connect() as conn:
            wrap = conn.execute(
                select(db.company.c.website_domain).where(
                    db.company.c.display_name == "Wrap Süd GmbH"
                )
            ).scalar()
        assert wrap == "wrap-sued.de"
        # Dubletten zusammenführen
        page.goto(f"{ui}/maintenance")
        block = page.locator(".card", has_text="Bekannt AG").first
        block.locator("select[name=keep]").select_option(str(known))
        block.locator("input[name=reason]").fill("gleiche Firma")
        block.locator("form[action='/maintenance/merge'] input[name=author]").fill("Vertrieb")
        with page.expect_navigation():
            block.locator("form[action='/maintenance/merge'] button").click()
        assert "zusammengeführt" in page.content()
        with engine.connect() as conn:
            assert repo.company_row(conn, dup)["merged_into_id"] == known
        # Stammdaten auf der Profilseite
        page.goto(f"{ui}/company/{known}#edit")
        form = page.locator("form[action$='/edit']", has=page.locator("input[value=country_code]"))
        form.locator("input[name=value]").fill("AT")
        form.locator("input[name=author]").fill("Vertrieb")
        with page.expect_navigation():
            form.locator("button").click()
        assert "„DE“ → „AT“" in page.content()
        browser.close()
    assert errors == []


def test_upload_requires_token_and_rejects_bad_list(ui: str) -> None:
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\nfalsch\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n'
        "Content-Type: text/csv\r\n\r\nname\nA\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    request = urllib.request.Request(
        f"{ui}/imports",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(request)
    assert err.value.code == 403
    page = urllib.request.urlopen(f"{ui}/imports").read().decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
    data = urllib.parse.urlencode({"csrf": token, "list_name": "gibt es nicht"}).encode()
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{ui}/imports/research", data=data)
    assert err.value.code == 400
