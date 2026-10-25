"""Phase 6: Rollenklassifikation, Belegklassen, Belegstatus – mit echten Texten aus dem Live-Test.

Echte Texte: ``tests/phase6_real_texts.py`` (wörtlich, abgerufen 2026-10-23). Wo die sechs
Testfirmen eine Regel nicht abdecken (Händler, Verneinung, Widerspruch), sind die Sätze
ausdrücklich als SYNTHETISCH markiert – sie behaupten nichts über reale Unternehmen.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, insert

from market_intel import db, repo
from market_intel.engine.extract import extract_company_documents
from market_intel.opportunity.evidence import Document, EvidenceIndex, Hit, StatementRule
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.model import check, load_model
from market_intel.opportunity.report import opportunity_report, to_markdown
from market_intel.opportunity.roles import classify_roles, hit_class, load_roles, name_tokens
from market_intel.opportunity.run import assess_all_applications, assess_and_store
from market_intel.opportunity.watch import set_status
from market_intel.products import load_products, matrix_status
from market_intel.sources.user_data import import_company_list
from market_intel.ui.render import opportunity_page

from . import phase6_real_texts as real
from .fakeweb import FakeWeb
from .opportunity_seed import _website_document

RULES = load_roles()
WHEN = datetime(2026, 10, 23, tzinfo=UTC)
AVERY = "https://graphics.averydennison.com/en/home"
UMDASCH = "https://www.umdasch.com"


def _texts(pages: dict[str, list[str]]) -> list[tuple[Document, str]]:
    """Seiten wie gespeichert: je Eintrag eine Zeile (so trennt die Extraktion sie auch)."""
    return [
        (Document(i, url, "website", WHEN, url), "\n".join(lines))
        for i, (url, lines) in enumerate(pages.items(), 1)
    ]


def _html(lines: list[str]) -> str:
    items = "".join(
        f"<p>{t}</p>" if StatementRule()(t) else f"<ul><li>{t}</li></ul>" for t in lines
    )
    return f"<html><body>{items}</body></html>"


def _source_document(engine: Engine, source_key: str, url: str) -> int:
    """Testdokument einer Quelle (Register oder Nutzerliste) – nur als Herkunft der Kennung."""
    with engine.begin() as conn:
        return int(
            conn.execute(
                insert(db.source_document).values(
                    source_key=source_key,
                    url=url,
                    final_url=url,
                    request_body_sha256="",
                    title=url,
                    retrieved_at=db.utcnow(),
                    http_status=200,
                    content_type="text/plain",
                    sha256=url,
                    body=b"Testdokument",
                )
            ).inserted_primary_key[0]
        )


def _company(
    engine: Engine,
    ctx: Any,
    cases: dict[str, Any],
    name: str,
    pages: dict[str, list[str]],
    *,
    website: str | None = None,
    country: str = "DE",
    lei_source: str | None = "gleif",
) -> int:
    """Firma mit gespeicherten Seiten anlegen, Extraktion laufen lassen (wie im Live-Lauf)."""
    url = website or next(iter(pages), None)
    with engine.begin() as conn:
        company_id = repo.create_company(conn, name, country_code=country, website=url)
    if lei_source:
        register = _source_document(engine, lei_source, f"https://register.test/{company_id}")
        with engine.begin() as conn:
            repo.add_identifier(conn, company_id, "lei", f"TESTLEI{company_id:013d}", register)
    docs = [_website_document(engine, page, _html(lines)) for page, lines in pages.items()]
    if docs:
        extract_company_documents(ctx, company_id, docs, cases, [])
        PAGES[company_id] = docs
        linked = {d.document_id for d in EvidenceIndex(engine, company_id).documents}
        assert set(docs) <= linked, "Testseiten sind der Firma nicht zugeordnet"
    return company_id


# Live waren alle abgerufenen Seiten der Firma zugeordnet (über die auf jeder Seite wiederholte
# Navigation mit Zielgruppen-/Vertriebsangaben). Die Testseiten enthalten nur die zitierten
# Zeilen – Navigation wird nicht erfunden; stattdessen gilt jede gespeicherte Seite als
# zugeordnet, wie im Live-Lauf.
PAGES: dict[int, list[int]] = {}


@pytest.fixture
def setup(
    engine: Engine, make_ctx: Any, cases: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from market_intel.opportunity import evidence

    original = evidence.company_documents
    PAGES.clear()

    def with_pages(engine_: Engine, company_id: int) -> list[Document]:
        found = original(engine_, company_id)
        known = {d.document_id for d in found}
        with engine_.connect() as conn:
            rows = conn.execute(
                db.source_document.select().where(
                    db.source_document.c.id.in_(PAGES.get(company_id, []))
                )
            ).mappings()
            extra = [
                Document(r["id"], r["url"], r["source_key"], r["retrieved_at"], r["title"] or "")
                for r in rows
                if r["id"] not in known
            ]
        return found + extra

    monkeypatch.setattr(evidence, "company_documents", with_pages)
    repo.sync_sources(engine, (), (), False)
    return {
        "ctx": make_ctx(FakeWeb()),
        "cases": cases,
        "catalog": load_products(),
        "model": load_model(),
    }


def _best(engine: Engine, setup: dict[str, Any], company_id: int) -> dict[str, Any]:
    return assess_and_store(engine, [company_id], setup["cases"], setup["catalog"], setup["model"])[
        company_id
    ]


def _case(engine: Engine, setup: dict[str, Any], company_id: int, key: str) -> dict[str, Any]:
    results = assess_all_applications(
        engine, company_id, setup["cases"], setup["catalog"], setup["model"]
    )
    return next(r for r in results if r["use_case"] == key)


# ---------------------------------------------------------------------- Belegklassen


def test_menu_items_titles_and_product_names_are_not_sentences() -> None:
    rule = RULES.statement
    for fragment in (
        "Banners",
        "Vehicle Wrapping Films",
        "Paint Protection Films",
        "Logistik, Rollout und Montage",
        "Projektleiter / Projektmanager (m/w/d) Ladenbau",
    ):
        assert not rule(fragment), fragment
    assert rule(real.UMDASCH["https://www.umdasch.com/karriere"][0])
    assert rule("Wir bieten Vollfolierung, Teilfolierung und Flottenbeschriftung an.")


@pytest.mark.parametrize(
    ("quote", "term", "name", "expected"),
    [
        ("Vehicle Wrapping Films", "vehicle wrap", "Avery Dennison", "fragment"),
        (real.AVERY[f"{AVERY}.html"][2], "PPF", "Avery Dennison", "produktbezug"),
        (real.AVERY[f"{AVERY}/news/press-releases.html"][0], "window film", "Avery", "zielgruppe"),
        (real.AVERY[f"{AVERY}/news/newsletter.html"][0], "window film", "Avery", "erwaehnung"),
        (real.AVERY[f"{AVERY}/about-us.html"][0], "vehicle wrap", "Avery Dennison", "taetigkeit"),
        (real.UMDASCH[f"{UMDASCH}/karriere"][0], "Ladenbau", "Umdasch Group AG", "taetigkeit"),
        (real.UMDASCH[f"{UMDASCH}/de/"][4], "Ladenbau", "Umdasch Group AG", "erwaehnung"),
    ],
)
def test_hit_classes_on_real_quotes(quote: str, term: str, name: str, expected: str) -> None:
    document = Document(1, "https://x.test/", "website", WHEN, "")
    hit = Hit(term, quote, document, False, RULES.statement(quote))
    assert hit_class(hit, RULES, name_tokens(name, RULES)) == expected


# ---------------------------------------------------------------------- Rollen


@pytest.mark.parametrize(
    ("name", "pages", "roles"),
    [
        ("ORAFOL Europe GmbH", real.ORAFOL, {"hersteller"}),
        ("Avery Dennison", real.AVERY, set()),
        ("Umdasch Group AG", real.UMDASCH, {"verarbeiter", "hersteller"}),
        ("Steinel GmbH", real.STEINEL, set()),
        ("DEUTSCHE BANK AKTIENGESELLSCHAFT", real.DEUTSCHE_BANK, set()),
    ],
)
def test_roles_from_real_texts(name: str, pages: dict[str, list[str]], roles: set[str]) -> None:
    profile = classify_roles(_texts(pages), name, RULES)
    assert {r for r in ("verarbeiter", "hersteller", "haendler") if profile.has(r)} == roles
    for items in profile.roles.values():
        for item in items:  # jeder Rollenbeleg ist ein wörtlicher Satz der Seite
            assert any(item.quote == line for lines in pages.values() for line in lines)


def test_installer_network_and_training_are_hints_not_processing() -> None:
    """Avery: „…network of … Certified Wrap Installers … installation services“ ist ein
    Partnernetz, „training and certification program … installation“ eine Schulung."""
    profile = classify_roles(_texts(real.AVERY), "Avery Dennison", RULES)
    assert not profile.has("verarbeiter")
    hints = [h["quote"] for h in profile.hints.get("zielgruppe_verarbeiter", [])]
    assert any("Certified Wrap Installers" in q for q in hints)
    assert profile.key == "unklar"


def test_third_party_manufacturer_sentence_is_no_role() -> None:
    """db.com: „…the German company Claas is … one of the world’s leading manufacturers…“."""
    profile = classify_roles(_texts(real.DEUTSCHE_BANK), "DEUTSCHE BANK AKTIENGESELLSCHAFT", RULES)
    assert not profile.has("hersteller")


def test_roles_are_independent_and_multiple() -> None:
    """Umdasch: ein Satz belegt Fertigung UND Montage; jede Rolle mit eigenem Beleg."""
    profile = classify_roles(_texts(real.UMDASCH), "Umdasch Group AG", RULES)
    assert profile.key == "mehrfach"
    assert "mehrere Rollen" in profile.label(RULES)
    cues = {e.cue.lower() for e in profile.roles["verarbeiter"]}
    assert cues & {"montage", "installation"}
    assert all(e.basis.startswith("Selbstbezug") for e in profile.roles["verarbeiter"])


def test_trader_negation_and_sales_channel_synthetic() -> None:
    """SYNTHETISCH (keine reale Firma): Händlerbeleg, Verkauf über Handel, Verneinung."""
    pages = {
        "https://synthetisch.test/": [
            "Wir sind Großhändler für Folien und Werkzeuge der Werbetechnik.",
            "Unsere Produkte liefern wir über den Großhandel in ganz Europa.",
            "Wir montieren keine Folien bei Kunden vor Ort.",
        ]
    }
    profile = classify_roles(_texts(pages), "Synthetisch GmbH", RULES)
    assert profile.has("haendler") and not profile.has("verarbeiter")
    assert [e.quote for e in profile.roles["haendler"]] == [pages["https://synthetisch.test/"][0]]


def test_sentence_preference_picks_activity_sentence_synthetic(engine: Engine) -> None:
    """SYNTHETISCH: Menüpunkt, dann Fremdsatz, dann eigener Satz auf derselben Seite → der eigene
    Satz wird Beleg (Live: umdasch.com zeigte zuerst den Menüpunkt „Ladenbau“)."""
    pages = {
        "https://synthetisch.test/": [
            "Ladenbau",
            "Die Branche erwartet für den Ladenbau ein ruhiges Jahr.",
            "Wir übernehmen Ladenbau und Montage für Filialisten in Österreich.",
        ]
    }
    index = EvidenceIndex.__new__(EvidenceIndex)
    index.documents = [d for d, _ in _texts(pages)]
    index.texts = _texts(pages)
    tokens = name_tokens("Synthetisch GmbH", RULES)
    index.prefer = lambda h: hit_class(h, RULES, tokens) == "taetigkeit"
    assert [h.quote for h in index.hits(("Ladenbau",))] == [pages["https://synthetisch.test/"][2]]


# ---------------------------------------------------------------------- Bewertung echter Fälle


def test_manufacturer_with_product_names_needs_manual_review(
    engine: Engine, setup: dict[str, Any]
) -> None:
    """ORAFOL: nur Menüpunkte zur Folienanwendung + Herstellungssätze → kein Verarbeiter, kein
    hoher Fit – aber auch nicht ausgeschlossen (Werkzeugbedarf unbekannt)."""
    cid = _company(engine, setup["ctx"], setup["cases"], "ORAFOL Europe GmbH", real.ORAFOL)
    best = _best(engine, setup, cid)
    a = best["assessment"]
    assert a["fit_status"] == "unklar"
    assert best["segment"]["key"] == "manuelle_pruefung"
    assert 0 < best["scores"]["product_relevance"] < 35
    assert a["roles"]["roles"]["hersteller"]["evidenced"]
    assert not a["roles"]["roles"]["verarbeiter"]["evidenced"]
    reasons = " ".join(a["manual_review_reasons"])
    assert "Hersteller ohne belegte eigene Verarbeitung" in reasons
    assert "nicht belegt" in best["hypothesis"]["statement"]
    lead = next(r for r in lead_rows(engine) if r["firma_id"] == cid)  # nicht ausgeschlossen
    assert lead["manuelle_pruefung"].startswith("ja") and "Hersteller" in lead["rolle"]


def test_avery_texts_are_not_rated_as_processor(engine: Engine, setup: dict[str, Any]) -> None:
    cid = _company(engine, setup["ctx"], setup["cases"], "Avery Dennison", real.AVERY, country="US")
    for key in ("lackschutz_aufbereitung", "fahrzeugfolierung", "glasfolien"):
        result = _case(engine, setup, cid, key)
        assert result["assessment"]["fit_status"] in ("hinweis", "unklar"), key
        assert result["scores"]["product_relevance"] < 60, key
        assert result["segment"]["key"] == "manuelle_pruefung", key
        assert not result["assessment"]["processing_evidenced"]


def test_umdasch_processing_is_evidenced_and_menu_items_add_nothing(
    engine: Engine, setup: dict[str, Any]
) -> None:
    ctx, cases = setup["ctx"], setup["cases"]
    cid = _company(engine, ctx, cases, "Umdasch Group AG", real.UMDASCH, country="AT")
    with_menu = _case(engine, setup, cid, "ladenbau_messebau")
    assert with_menu["assessment"]["fit_status"] == "belegt"
    assert with_menu["scores"]["product_relevance"] >= 35
    without = {u: [t for t in lines if RULES.statement(t)] for u, lines in real.UMDASCH.items()}
    copy_pages = {u.replace("umdasch.com", "ohne-menue.test"): v for u, v in without.items() if v}
    cid2 = _company(engine, ctx, cases, "Umdasch Group AG", copy_pages, country="AT")
    plain = _case(engine, setup, cid2, "ladenbau_messebau")
    # Menüpunkte „Ladenbau“, „Store Design“, „Logistik, Rollout und Montage“ ändern nichts
    assert plain["scores"]["product_relevance"] == with_menu["scores"]["product_relevance"]


def test_steinel_and_bank_texts_give_no_fit(engine: Engine, setup: dict[str, Any]) -> None:
    impressum = {
        "https://www.steinel.de/de/impressum/": [
            "Soweit nicht anders angegeben, sind alle Markenzeichen auf STEINEL Websites "
            "markenrechtlich geschützt.",
            "Dies gilt insbesondere für STEINEL Marken, Typenschilder, Firmenlogos und Embleme.",
        ]
    }
    steinel = _company(
        engine, setup["ctx"], setup["cases"], "Steinel GmbH", real.STEINEL | impressum
    )
    bank = _company(
        engine, setup["ctx"], setup["cases"], "DEUTSCHE BANK AKTIENGESELLSCHAFT", real.DEUTSCHE_BANK
    )
    for cid in (steinel, bank):
        best = _best(engine, setup, cid)
        assert best["scores"]["product_relevance"] == 0
        assert best["assessment"]["fit_status"] == "kein_beleg"
        assert best["segment"]["key"] == "kein_produkt_fit"


def test_blocked_website_means_no_data_not_no_need(engine: Engine, setup: dict[str, Any]) -> None:
    """Sixt: Website verweigerte den Abruf (403) → „Keine Daten“, nicht „kein Bedarf“."""
    sixt = _company(
        engine, setup["ctx"], setup["cases"], "Sixt SE", {}, website="https://www.sixt.de/"
    )
    nosite = _company(engine, setup["ctx"], setup["cases"], "Ohne Website AG", {}, website=None)
    with engine.begin() as conn:
        for cid, status, detail in (
            (
                sixt,
                "failed",
                "https://www.sixt.de/: blocked_policy Host verweigerte den Zugriff "
                "(HTTP 403) – keine weiteren Abrufe dieses Hosts in diesem Lauf",
            ),
            (nosite, "not_applicable", "keine Website bekannt (keine Websuche verfügbar)"),
        ):
            conn.execute(
                insert(db.investigation_log).values(
                    company_id=cid,
                    adapter_key="website",
                    run_id=None,
                    status=status,
                    detail=detail,
                    finished_at=db.utcnow(),
                )
            )
    for cid, data_status in ((sixt, "website_gesperrt"), (nosite, "keine_website")):
        best = _best(engine, setup, cid)
        assert best["assessment"]["fit_status"] == "keine_daten"
        assert best["assessment"]["data_status"]["key"] == data_status
        assert best["segment"]["key"] == "keine_daten"
        assert "nicht ausgeschlossen" in best["hypothesis"]["statement"]
        assert best["scores"]["priority"] == 0
    rows = {r["firma"]: r for r in lead_rows(engine, include_no_fit=True)}
    assert rows["Sixt SE"]["segment"].startswith("Keine Daten")
    assert "Sixt SE" not in {r["firma"] for r in lead_rows(engine)}  # kein Lead ohne Belege


def test_activity_without_processing_is_capped_below_high_synthetic(
    engine: Engine, setup: dict[str, Any]
) -> None:
    """SYNTHETISCH: drei Tätigkeitsbegriffe in einem eigenen Satz, aber kein Verarbeitungs-/
    Montagebeleg → Rohwert ≥ 60, gedeckelt auf 59 („hinweis“, manuelle Prüfung)."""
    pages = {
        "https://hinweis.test/": [
            "Unsere Kompetenz umfasst Messebau, Ladenbau und Shopdesign für internationale Marken."
        ]
    }
    cid = _company(engine, setup["ctx"], setup["cases"], "Hinweis GmbH", pages)
    result = _case(engine, setup, cid, "ladenbau_messebau")
    a = result["assessment"]
    assert a["fit_status"] == "hinweis" and not a["processing_evidenced"]
    assert a["relevance_uncapped"] >= 60 and a["capped"]
    assert result["scores"]["product_relevance"] == a["cap"] == 59
    assert result["segment"]["key"] == "manuelle_pruefung"


def test_contradictory_evidence_needs_review_synthetic(
    engine: Engine, setup: dict[str, Any]
) -> None:
    """SYNTHETISCH: Folierung belegt, aber die eigene Seite nennt sich Folienhersteller."""
    pages = {
        "https://widerspruch.test/": [
            "Wir übernehmen Vollfolierung und Teilfolierung für Fuhrparks im Rheinland.",
            "Als Folienhersteller beliefern wir Werbetechniker in ganz Europa.",
        ]
    }
    cid = _company(engine, setup["ctx"], setup["cases"], "Widerspruch GmbH", pages, lei_source=None)
    result = _case(engine, setup, cid, "fahrzeugfolierung")
    assert result["assessment"]["fit_status"] == "widerspruechlich"
    assert result["scores"]["product_relevance"] < 60
    assert result["segment"]["key"] == "manuelle_pruefung"
    assert result["hypothesis"]["counter_evidence"]
    assert result["assessment"]["identity"]["key"] == "ohne_kennung"


def test_same_product_name_and_word_count_once_synthetic(
    engine: Engine, setup: dict[str, Any]
) -> None:
    """SYNTHETISCH: „Lackschutzfolie (PPF)“ ist ein Name; „Lackschutz“ (Prozessbegriff) steckt
    im Tätigkeitsbegriff „Lackschutzfolie“ – beides zählt nicht doppelt."""
    pages = {"https://ppf.test/": ["Wir montieren Lackschutzfolie (PPF) auf Neufahrzeugen."]}
    cid = _company(engine, setup["ctx"], setup["cases"], "PPF Werkstatt GmbH", pages)
    parts = _case(engine, setup, cid, "lackschutz_aufbereitung")["scores"]
    activity = parts["product_relevance_parts"]["activity_evidence"]
    process = parts["product_relevance_parts"]["process_evidence"]
    assert activity["basis"].startswith("1 Tätigkeitsbeleg")  # nicht 2
    assert "lackschutz" not in process["basis"].lower()


# ---------------------------------------------------------------------- Modell, Matrix, Logik


def test_caps_must_stay_below_segment_thresholds() -> None:
    raw = copy.deepcopy(load_model().raw)
    assert check(raw) == []
    raw["product_relevance"]["evidence_caps"]["unklar"] = 40
    raw["product_relevance"]["evidence_caps"]["hinweis"] = 75
    problems = check(raw)
    assert any("unklar" in p for p in problems) and any("hinweis" in p for p in problems)
    del raw["product_relevance"]["evidence_caps"]["keine_daten"]
    assert any("fehlt keine_daten" in p for p in check(raw))


def test_unverified_product_matrix_is_shown_as_unverified(
    engine: Engine, setup: dict[str, Any]
) -> None:
    status = matrix_status(setup["catalog"])
    assert not status["verified"] and status["label"].startswith("NICHT verifiziert")
    confirmed = dataclasses.replace(
        setup["catalog"],
        products={
            k: dataclasses.replace(g, status="confirmed")
            for k, g in setup["catalog"].products.items()
        },
    )
    assert matrix_status(confirmed)["verified"]
    one = dict(setup["catalog"].products)
    first = next(iter(one))
    one[first] = dataclasses.replace(one[first], status="confirmed")
    assert not matrix_status(dataclasses.replace(setup["catalog"], products=one))["verified"]
    cid = _company(engine, setup["ctx"], setup["cases"], "Umdasch Group AG", real.UMDASCH)
    _best(engine, setup, cid)
    report = opportunity_report(engine, cid, setup["cases"], setup["catalog"], setup["model"])
    assert "NICHT verifiziert" in to_markdown(report)
    assert "NICHT verifiziert" in opportunity_page(report, [], "csrf", ("vorgemerkt",))
    assert all(r["produktmatrix"].startswith("NICHT verifiziert") for r in lead_rows(engine))


def test_customer_and_rejection_still_take_precedence(
    engine: Engine, setup: dict[str, Any], tmp_path: Any
) -> None:
    """Bestandskunden- und Ausschlusslogik unverändert, auch für die neuen Status."""
    orafol = _company(engine, setup["ctx"], setup["cases"], "ORAFOL Europe GmbH", real.ORAFOL)
    sixt = _company(
        engine, setup["ctx"], setup["cases"], "Sixt SE", {}, website="https://www.sixt.de/"
    )
    path = tmp_path / "kunden.csv"
    path.write_text("name;website\nORAFOL Europe GmbH;https://www.orafol.com/en\n", "utf-8")
    import_company_list(
        engine, path, list_name="Bestandskunden", source_url="intern:crm", publisher=None
    )
    set_status(engine, sixt, "verworfen", reason="kein Bedarf", author="Vertrieb")
    assert _best(engine, setup, orafol)["segment"]["key"] == "bestandskunde"
    assert _best(engine, setup, sixt)["segment"]["key"] == "verworfen"
    names = {r["firma"] for r in lead_rows(engine, include_no_fit=True)}
    assert "ORAFOL Europe GmbH" not in names and "Sixt SE" not in names
    everything = {r["firma"] for r in lead_rows(engine, include_no_fit=True, include_excluded=True)}
    assert {"ORAFOL Europe GmbH", "Sixt SE"} <= everything


def test_identity_user_given_lei_is_flagged(engine: Engine, setup: dict[str, Any]) -> None:
    """Avery: LEI kam aus der Nutzerliste, GLEIF bestätigte ihn – Auswahl bleibt beim Nutzer."""
    ctx, cases = setup["ctx"], setup["cases"]
    cid = _company(engine, ctx, cases, "Avery Dennison", real.AVERY, lei_source="user_list")
    register = _source_document(engine, "gleif", "https://api.gleif.org/test")
    with engine.begin() as conn:
        repo.add_identifier(conn, cid, "gleif_ra", "RA000602:834948", register)
    best = _best(engine, setup, cid)
    assert best["assessment"]["identity"]["key"] == "nutzer_register"
    assert any("Firmenzuordnung" in r for r in best["assessment"]["manual_review_reasons"])


def test_manufacturer_site_check_is_recorded_and_consistent(cases: dict[str, Any]) -> None:
    """Abgleich mit yellotools.com am 2026-10-25: 38 von 44 Beispielnamen wörtlich gefunden;
    nichts gelöscht, Status bleibt „researched“ (Bestätigung nur durch Yellotools selbst)."""
    from market_intel.products import export_csv, import_csv, validate

    catalog = load_products()
    assert validate(catalog, cases) == []
    status = matrix_status(catalog)
    assert (status["examples_found"], status["examples_checked"]) == (38, 44)
    assert "38 von 44" in status["label"] and status["label"].startswith("NICHT verifiziert")
    assert all(g.status == "researched" and g.source_checked_at for g in catalog.products.values())
    assert "StreetRoller Basic" in catalog.products["wand_bodenroller"].examples  # nicht gelöscht
    assert "StreetRoller Basic" in catalog.products["wand_bodenroller"].examples_not_found
    roundtrip, _ = import_csv(export_csv(catalog), catalog)
    for key, group in catalog.products.items():  # Abgleichfelder überleben die CSV-Pflege
        again = roundtrip.products[key]
        assert again.examples_on_manufacturer_site == group.examples_on_manufacturer_site
        assert again.examples_not_found == group.examples_not_found
        assert again.source_checked_at == group.source_checked_at
    broken = dict(catalog.products)
    group = broken["magnete"]
    broken["magnete"] = dataclasses.replace(group, examples_not_found=("JoniMag Strip", "Erfunden"))
    problems = validate(dataclasses.replace(catalog, products=broken), cases)
    assert any("keine Beispiele" in p for p in problems)
    assert any("zugleich gefunden" in p for p in problems)


def test_stale_assessments_from_older_model_are_not_current(
    engine: Engine, setup: dict[str, Any]
) -> None:
    """Modellwechsel: Ein früherer (v2-)Fit für einen Anwendungsfall, der jetzt keinen Fit mehr
    hat, darf nicht als aktueller Lead stehen bleiben (Historie bleibt gespeichert)."""
    from market_intel.opportunity.assess import latest_assessments, store_assessment

    cid = _company(engine, setup["ctx"], setup["cases"], "Steinel GmbH", real.STEINEL)
    old = _case(engine, setup, cid, "fahrzeugfolierung")  # wie eine alte Bewertung mit Fit
    old["scores"] = {**old["scores"], "product_relevance": 81, "priority": 70}
    old["model_version"] = "v2-2026-10-20"
    old["segment"] = {**old["segment"], "key": "fit_hoch_wachstum_unklar"}
    store_assessment(engine, old, None)  # ohne Runde, wie vor Phase 6
    assert any(r["prioritaet"] == 70 for r in lead_rows(engine))  # Ausgangslage: Altlast
    best = _best(engine, setup, cid)  # neue Runde: kein Fit
    assert best["scores"]["product_relevance"] == 0
    assert all(r["firma_id"] != cid for r in lead_rows(engine))
    assert {r["use_case_key"] for r in latest_assessments(engine, cid)} == {"keiner"}
    with engine.connect() as conn:
        history = conn.execute(db.opportunity_score.select()).all()
    assert len(history) == 2  # nichts gelöscht
