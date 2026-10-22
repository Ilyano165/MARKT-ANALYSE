"""Abnahme Phase 4: Produktmatrix, Bedarfshypothesen, Bewertung, Segmente, Leads, Watchlist."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert

from market_intel import db, repo
from market_intel.intel.manual import document_plain_text
from market_intel.opportunity.assess import assess_company, latest_assessments
from market_intel.opportunity.leads import LEAD_COLUMNS, export_leads, lead_rows
from market_intel.opportunity.model import ModelError, check, load_model
from market_intel.opportunity.run import NO_FIT_KEY, assess_all_applications, assess_and_store
from market_intel.opportunity.watch import current_entries, history_of, set_status
from market_intel.products import (
    export_csv,
    import_csv,
    load_products,
    parse_catalog,
    to_toml,
    validate,
)
from market_intel.usecases import load_use_cases

from .fakeweb import FakeWeb
from .opportunity_seed import seed_candidates


@pytest.fixture
def world(engine: Engine, make_ctx: Any, cases: dict[str, Any]) -> dict[str, Any]:
    ctx = make_ctx(FakeWeb())
    ids = seed_candidates(engine, ctx, cases)
    catalog = load_products()
    model = load_model()
    best = assess_and_store(engine, list(ids.values()), cases, catalog, model)
    return {"ids": ids, "best": best, "catalog": catalog, "model": model, "cases": cases}


# ---------------------------------------------------------------------- Produktmatrix


def test_product_matrix_is_complete_and_cross_referenced() -> None:
    catalog = load_products()
    cases = load_use_cases()
    assert validate(catalog, cases) == []
    assert len(catalog.products) >= 10
    for group in catalog.products.values():
        assert group.sources and all(s.startswith("https://") for s in group.sources)
        assert group.limitations, f"{group.key}: Einsatzgrenzen fehlen"
        assert group.status == "researched"  # nicht als bestätigt ausgegeben
    # Branchen aus dem Auftrag und abgeleitete Branchen sind abgedeckt
    origins = {c.origin for c in cases.values()}
    assert {"auftrag", "abgeleitet"} <= origins and len(cases) >= 20
    assert all(c.evidence_status == "hypothesis" for c in cases.values())


def test_product_maintenance_roundtrip_and_validation(tmp_path: Path) -> None:
    catalog = load_products()
    text = export_csv(catalog)
    rows = list(csv.DictReader(text.splitlines()))
    assert rows[0]["key"] == "rakel_kunststoff"
    edited = text.replace("Kunststoffrakel und Rakelsets", "Kunststoffrakel (geprüft)")
    edited += (
        '"neu_x","Neues Produkt","draft","Beschreibung","","Folie","Prozess","Werbetechniker",'
        '"","werbetechnik_beschriftung","","","Folie","https://example.test/produkt","",""\n'
    )
    updated, notes = import_csv(edited, catalog)
    assert updated.products["rakel_kunststoff"].name == "Kunststoffrakel (geprüft)"
    assert "neu_x" in updated.products and any("neu" in n for n in notes)
    reparsed = parse_catalog(to_toml(updated))
    assert reparsed.products.keys() == updated.products.keys()
    broken = parse_catalog(
        '[[product]]\nkey = "x"\nname = "X"\nstatus = "confirmed"\ndescription = "d"\n'
        'materials = ["m"]\nprocesses = ["p"]\ntypical_users = ["u"]\n'
        'sources = ["ohne-url"]\napplications = ["gibt_es_nicht"]\n'
    )
    problems = validate(broken, load_use_cases())
    assert any("confirmed_by" in p for p in problems)
    assert any("Quelle ohne URL" in p for p in problems)
    assert any("existiert nicht" in p for p in problems)


def test_model_rules_product_fit_outweighs_size() -> None:
    model = load_model()
    raw = dict(model.raw)
    assert check(raw) == []
    bad = json.loads(json.dumps(raw))
    bad["model"]["priority_weights"] = {"product_relevance": 0.3, "business_potential": 0.7}
    problems = check(bad)
    assert any("mindestens 50 %" in p for p in problems)
    bad["segment"][-1]["conditions"] = [["product_relevance", ">=", 1]]
    assert any("Auffangsegment" in p for p in check(bad))
    with pytest.raises(ModelError):
        path = Path(__file__).parent / "_tmp_model.toml"
        try:
            path.write_text('[model]\nversion="x"\npriority_weights={product_relevance=1.0}\n')
            load_model(path)
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------- Hypothesen


def test_hypotheses_across_industries_are_backed_by_real_quotes(
    world: dict[str, Any], engine: Engine
) -> None:
    expected = {
        "folierer": "fahrzeugfolierung",
        "messebau": "ladenbau_messebau",
        "remarketing": "entfolierung_fuhrpark",
        "glas": "glasfolien",
    }
    for key, use_case in expected.items():
        best = world["best"][world["ids"][key]]
        assert best["use_case"] == use_case, (key, best["use_case"])
        hyp = best["hypothesis"]
        assert hyp["activity_evidence"], key
        for item in hyp["activity_evidence"] + hyp["process_evidence"]:
            text = document_plain_text(engine, item["document_id"])
            assert " ".join(item["quote"].split()) in " ".join(text.split())  # wörtlich belegt
            assert item["url"] and item["retrieved_at"]
        assert "nicht belegt" in hyp["statement"]  # keine Behauptung über Bedarf/Kaufbereitschaft
        assert hyp["products"] and all(p["sources"] for p in hyp["products"])
        assert best["scores"]["product_relevance"] >= 35


def test_derived_industry_is_marked_as_new_and_unconfirmed(world: dict[str, Any]) -> None:
    best = world["best"][world["ids"]["remarketing"]]
    assert best["use_case_origin"] == "abgeleitet" and not best["core_application"]
    assert "abgeleitet" in best["scores"]["product_relevance_parts"]["application_status"]["basis"]
    assert any("Vertrieb bestätigen" in s for s in best["next_steps"])


def test_large_company_without_fit_is_rated_zero(world: dict[str, Any], engine: Engine) -> None:
    bank = world["best"][world["ids"]["bank"]]
    assert bank["scores"]["product_relevance"] == 0
    assert bank["scores"]["priority"] == 0
    assert bank["segment"]["key"] == "kein_produkt_fit"
    assert bank["facts"]["size_class"] == "large"  # groß – trotzdem kein Lead
    assert "zu keinem der" in bank["hypothesis"]["statement"]
    folierer = world["best"][world["ids"]["folierer"]]
    assert folierer["scores"]["priority"] > bank["scores"]["priority"]
    names = [r["firma"] for r in lead_rows(engine)]
    assert "Großbank Finanz AG" not in names


def test_llm_claims_do_not_create_product_fit(world: dict[str, Any], engine: Engine) -> None:
    """Eine (z. B. von einer KI) gespeicherte Fit-Behauptung ohne Tätigkeitsbeleg zählt nicht."""
    bank_id = world["ids"]["bank"]
    with engine.begin() as conn:
        conn.execute(
            insert(db.company_product_fit).values(
                company_id=bank_id,
                use_case_key="fahrzeugfolierung",
                fit_level="strong",
                rationale="KI: Die Bank betreibt sicher eine Fahrzeugflotte und braucht Rakel.",
                evidence_ids=json.dumps([]),
                assessed_by="llm:test",
                assessed_at=db.utcnow(),
            )
        )
    case = world["cases"]["fahrzeugfolierung"]
    result = assess_company(engine, bank_id, case, world["catalog"], world["model"])
    assert result["scores"]["product_relevance"] == 0 and result["scores"]["priority"] == 0
    assert "sicher" not in result["hypothesis"]["statement"]


def test_missing_data_is_not_penalized(world: dict[str, Any]) -> None:
    unknown = world["best"][world["ids"]["folien_ohne_daten"]]
    tiny = world["best"][world["ids"]["folien_klein"]]
    assert unknown["scores"]["product_relevance"] == tiny["scores"]["product_relevance"] > 0
    assert unknown["scores"]["business_potential"] is None  # unbekannt, nicht 0
    assert "neutral" in unknown["scores"]["priority_basis"]
    assert unknown["scores"]["priority"] >= tiny["scores"]["priority"]
    assert unknown["scores"]["data_quality"] < 100
    assert "Mitarbeiterzahl" in unknown["gaps"]


def test_growth_signals_are_classified(world: dict[str, Any]) -> None:
    folierer = world["best"][world["ids"]["folierer"]]
    bases = {s["kind"]: s["basis"] for s in folierer["growth"]["signals"]}
    assert bases["new_site"] == "confirmed"
    assert bases["trend"] == "derived"
    assert folierer["growth"]["status"] == "strong"  # 40 → 52 Mitarbeiter = +30 %
    assert folierer["growth"]["expansion"] == "confirmed"
    assert folierer["segment"]["key"] == "fit_hoch_wachstum_stark"
    parts = folierer["scores"]["business_potential_parts"]
    assert set(parts) >= {"size", "growth", "expansion", "reachability"}
    assert all("basis" in p and "weight" in p and "contribution" in p for p in parts.values())


def test_scores_are_separated_and_explained(world: dict[str, Any]) -> None:
    result = world["best"][world["ids"]["messebau"]]
    scores = result["scores"]
    assert {"product_relevance", "business_potential", "data_quality", "priority"} <= set(scores)
    total = sum(p["contribution"] for p in scores["product_relevance_parts"].values())
    assert abs(total - scores["product_relevance"]) <= 1
    assert "Keine Verkaufsprognose" in scores["disclaimer"]
    assert result["segment"]["reasons"]


def test_all_applications_are_checked(world: dict[str, Any], engine: Engine) -> None:
    results = assess_all_applications(
        engine, world["ids"]["folierer"], world["cases"], world["catalog"], world["model"]
    )
    assert len(results) == len(world["cases"])
    with_fit = {r["use_case"] for r in results if r["scores"]["product_relevance"] > 0}
    assert {"fahrzeugfolierung", "lackschutz_aufbereitung"} <= with_fit


# ---------------------------------------------------------------------- Watchlist, Leads


def test_watchlist_history_and_lead_export(
    world: dict[str, Any], engine: Engine, tmp_path: Path
) -> None:
    folierer = world["ids"]["folierer"]
    set_status(engine, folierer, "vorgemerkt", reason="Messekontakt", author="Vertrieb")
    set_status(engine, folierer, "in_pruefung", reason="Prozess klären", author="Vertrieb")
    with pytest.raises(ValueError):
        set_status(engine, folierer, "gekauft")
    assert [e["status"] for e in history_of(engine, folierer)] == ["in_pruefung", "vorgemerkt"]
    assert current_entries(engine)[0]["status"] == "in_pruefung"
    files = export_leads(engine, tmp_path)
    assert [f.name for f in files] == ["leads.csv", "leads.xlsx", "leads.json"]
    data = json.loads(files[2].read_text(encoding="utf-8"))["leads"]
    assert data and set(LEAD_COLUMNS) == set(data[0])
    row = next(r for r in data if r["firma_id"] == folierer)
    assert row["bewertungsstatus"] == "in Prüfung"
    assert "fahrzeugfolierung" not in row["produktgruppen"] and row["produktgruppen"]
    assert row["quellenlinks"].startswith("https://folienprofi.test")
    assert "52 Personen (2023" in row["mitarbeiter"]
    assert all(r["firma"] != "Großbank Finanz AG" for r in data)
    with_no_fit = lead_rows(engine, include_no_fit=True)
    bank = next(r for r in with_no_fit if r["firma"] == "Großbank Finanz AG")
    assert bank["prioritaet"] == 0 and bank["produktgruppen"] == ""


def test_assessment_history_is_kept(world: dict[str, Any], engine: Engine) -> None:
    ids = list(world["ids"].values())
    assess_and_store(engine, ids, world["cases"], world["catalog"], world["model"])
    with engine.connect() as conn:
        count = conn.execute(db.opportunity_score.select()).all()
    assert len(count) >= 2 * len(ids)  # zweite Bewertung ergänzt, überschreibt nicht
    assert {r["use_case_key"] for r in latest_assessments(engine, world["ids"]["bank"])} == {
        NO_FIT_KEY
    }
    with engine.connect() as conn:
        assert repo.company_row(conn, world["ids"]["bank"])["display_name"] == "Großbank Finanz AG"


# ---------------------------------------------------------------------- Bericht, Oberfläche


def test_report_has_eleven_sections(world: dict[str, Any], engine: Engine) -> None:
    from market_intel.opportunity.report import SECTIONS, opportunity_report, to_markdown

    report = opportunity_report(
        engine, world["ids"]["messebau"], world["cases"], world["catalog"], world["model"]
    )
    text = to_markdown(report)
    for number, title in enumerate(SECTIONS, start=1):
        assert f"## {number}. {title}" in text
    assert "https://messewerk.test/" in text and "Keine Verkaufsprognose" in text
    bank = to_markdown(
        opportunity_report(
            engine, world["ids"]["bank"], world["cases"], world["catalog"], world["model"]
        )
    )
    assert "Kein Produkt-Fit belegt" in bank


def test_ui_opportunity_and_leads(world: dict[str, Any], engine: Engine) -> None:
    import re
    import threading
    import urllib.parse
    import urllib.request

    from market_intel.ui.server import make_server

    server = make_server(engine, "127.0.0.1", 0, (world["cases"], world["catalog"], world["model"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        folierer = world["ids"]["folierer"]
        page = urllib.request.urlopen(f"{base}/company/{folierer}/opportunity").read().decode()
        assert "11. Quellen und Datum" in page and "Hoher Produkt-Fit" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        data = urllib.parse.urlencode(
            {
                "csrf": token,
                "status": "vorgemerkt",
                "reason": "Test",
                "author": "V",
                "comment": "Ansprechpartner Werkstatt klären",
            }
        ).encode()

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(urllib.request.Request(f"{base}/company/{folierer}/watch", data=data))
        except urllib.error.HTTPError as exc:  # 303 ohne Weiterleitung
            assert exc.code == 303
        page = urllib.request.urlopen(f"{base}/company/{folierer}/opportunity").read().decode()
        assert "vorgemerkt" in page and "Ansprechpartner Werkstatt klären" in page
        leads = urllib.request.urlopen(f"{base}/leads").read().decode()
        assert "Folienprofi Fahrzeugdesign GmbH" in leads and "Großbank" not in leads
        csv_text = urllib.request.urlopen(f"{base}/leads.csv").read().decode("utf-8-sig")
        assert csv_text.splitlines()[0].startswith("firma;website;land")
        assert "vorgemerkt" in csv_text
    finally:
        server.shutdown()
        server.server_close()


def test_cli_products_and_leads(world: dict[str, Any], engine: Engine, tmp_path: Path) -> None:
    from market_intel.cli import main

    db_url = str(engine.url)
    assert main(["--db", db_url, "products", "validate"]) == 0
    assert main(["--db", db_url, "opportunity-model"]) == 0
    assert (
        main(
            [
                "--db",
                db_url,
                "watch",
                "--company-id",
                str(world["ids"]["glas"]),
                "--status",
                "in_pruefung",
                "--author",
                "V",
            ]
        )
        == 0
    )
    assert main(["--db", db_url, "leads", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "leads.xlsx").exists()
    assert (
        main(
            [
                "--db",
                db_url,
                "opportunity",
                "--company-id",
                str(world["ids"]["glas"]),
                "--md",
                str(tmp_path / "glas.md"),
            ]
        )
        == 0
    )
    assert "Sonnenschutzfolie" in (tmp_path / "glas.md").read_text(encoding="utf-8")


def test_single_known_component_does_not_inflate_potential(world: dict[str, Any]) -> None:
    """Nur das Sitzland bekannt → kein Potenzial 100 (Regression aus der Sichtprüfung)."""
    glas = world["best"][world["ids"]["glas"]]["scores"]
    folierer = world["best"][world["ids"]["folierer"]]["scores"]
    assert glas["business_potential"] is not None and glas["business_potential"] < 65
    unknown_parts = [k for k, v in glas["business_potential_parts"].items() if v.get("unknown")]
    assert set(unknown_parts) == set(glas["business_potential_unknown"])
    assert folierer["business_potential"] > glas["business_potential"]
    total = sum(p["contribution"] for p in glas["business_potential_parts"].values())
    assert abs(total - glas["business_potential"]) <= 1
