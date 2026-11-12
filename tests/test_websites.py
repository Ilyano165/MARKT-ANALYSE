# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
# Testdaten (HTML, API-Antworten) bleiben bewusst am Stück lesbar.
# ruff: noqa: E501
"""Phase G: Website-Ermittlung – Kandidaten, belegte Prüfung, Entscheidung, Integration.

Das Test-Internet (``SiteWeb``) bildet Firmenwebsites, Wikidata und Fehlerfälle nach. Es
beweist die Logik, nicht die echte Suche – die ist im Live-Lauf dokumentiert
(docs/IMPLEMENTATION/PHASE_PROGRESS.md, Phase G).
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from market_intel import db, repo, websites
from market_intel.context import Ctx
from market_intel.domain import ProvenanceClass
from market_intel.engine.budget import Budget
from market_intel.jobs import Runtime, Worker, create_company_job, get_job
from market_intel.opportunity.assess import latest_assessments
from market_intel.opportunity.leads import export_leads, lead_rows
from market_intel.opportunity.model import load_model
from market_intel.products import load_products
from market_intel.sources import ted

from .fakeweb import FakeWeb
from .test_opportunity import world  # noqa: F401  (Fixture)

IMPRESSUM = (
    "<html><head><title>Impressum – Muster Folien GmbH</title></head><body>"
    "<h1>Impressum</h1><p>Muster Folien GmbH<br>Hauptstraße 5<br>12345 Berlin</p>"
    "<p>Amtsgericht Charlottenburg HRB 99999 B</p><p>Telefon: +49 30 1234567</p>"
    "<p>Wir sind Ihr Partner für Fahrzeugfolierung und Car Wrapping.</p></body></html>"
)
HOME = (
    "<html><head><title>Muster Folien – Fahrzeugfolierung</title></head><body>"
    "<p>Fahrzeugfolierung in Berlin.</p><a href='/impressum'>Impressum</a></body></html>"
)


def _html(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


class SiteWeb(FakeWeb):
    """FakeWeb plus Firmenwebsites für die Website-Ermittlung."""

    def __init__(self, failing_hosts: frozenset[str] = frozenset(), wikidata: str = "ok") -> None:
        super().__init__(failing_hosts)
        self.wikidata_mode = wikidata
        self.pages: dict[str, httpx.Response | Callable[[], httpx.Response]] = {
            "https://www.muster-folien.de/": _html(HOME),
            "https://www.muster-folien.de/impressum": _html(IMPRESSUM),
            # gleichnamig klingende Firma an anderem Ort (abgeleitete Domain!)
            "https://musterfolien.de/": _html(
                "<html><body><p>Musterfolien Werbung UG</p><a href='/impressum'>Impressum</a></body></html>"
            ),
            "https://musterfolien.de/impressum": _html(
                "<html><body><p>Musterfolien Werbung UG, Marienplatz 1, 80331 München</p></body></html>"
            ),
            # Konzernmarke: nennt die Tochter, eigene Adresse
            "https://muster-group.com/": _html(
                "<html><body><p>Muster Group – unsere Marken: Muster Folien GmbH</p>"
                "<a href='/imprint'>Imprint</a></body></html>"
            ),
            "https://muster-group.com/imprint": _html(
                "<html><body><p>Muster Group SE, Ring 9, 20095 Hamburg, HRB 11111</p></body></html>"
            ),
            # Weiterleitung auf fremde Firma
            "https://muster-folien.com/": httpx.Response(
                301, headers={"location": "https://www.ganz-andere.de/"}
            ),
            "https://www.ganz-andere.de/": _html(
                "<html><body><p>Ganz Andere GmbH, 50667 Köln</p></body></html>"
            ),
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls[host] += 1
        if host in self.failing or "*" in self.failing:
            raise httpx.ConnectError("Testnetz: Verbindung verweigert", request=request)
        if host == "www.wikidata.org" and self.wikidata_mode != "ok":
            if self.wikidata_mode == "429":
                return httpx.Response(429, headers={"retry-after": "1"}, text="slow down")
            raise httpx.ConnectError("Wikidata nicht erreichbar", request=request)
        if host == "www.wikidata.org":
            params = request.url.params
            if params.get("action") == "wbsearchentities":
                hits = []
                if "Muster Folien" in params.get("search", ""):
                    hits = [
                        {"id": "Q9001", "label": "Muster Folien GmbH", "description": "Firma"},
                        {"id": "Q9002", "label": "Muster Folien GmbH", "description": "Firma AT"},
                    ]
                return httpx.Response(200, json={"search": hits})
            if request.url.path.startswith("/wiki/Special:EntityData/"):
                qid = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
                entities = {
                    "Q9001": _entity(
                        "Q9001", "Muster Folien GmbH", "Q183", "https://www.muster-folien.de"
                    ),
                    "Q9002": _entity(
                        "Q9002", "Muster Folien GmbH", "Q40", "https://www.muster-folien.at"
                    ),
                    "Q183": {"id": "Q183", "labels": {}, "claims": _string_claim("P297", "DE")},
                    "Q40": {"id": "Q40", "labels": {}, "claims": _string_claim("P297", "AT")},
                }
                if qid in entities:
                    return httpx.Response(200, json={"entities": {qid: entities[qid]}})
                return httpx.Response(404, text="not found")
        url = str(request.url)
        if request.url.path == "/robots.txt" and url not in self.pages:
            return httpx.Response(404)
        if url in self.pages:
            page = self.pages[url]
            return page() if callable(page) else page
        if host in {urllib.parse.urlsplit(u).hostname for u in self.pages}:
            return httpx.Response(404, text="nicht gefunden")
        if host.endswith((".de", ".com", ".at")) and host not in ("api.ted.europa.eu",):
            raise httpx.ConnectError(f"DNS: {host} unbekannt", request=request)
        self.calls[host] -= 1  # FakeWeb zählt selbst
        return super().handle(request)


def _string_claim(prop: str, value: str) -> dict[str, Any]:
    return {
        prop: [{"rank": "normal", "mainsnak": {"datavalue": {"value": value, "type": "string"}}}]
    }


def _entity(qid: str, label: str, country: str, site: str) -> dict[str, Any]:
    claims = {
        **_string_claim("P856", site),
        "P17": [
            {
                "rank": "normal",
                "mainsnak": {"datavalue": {"value": {"id": country}, "type": "wikibase-entityid"}},
            }
        ],
    }
    return {"id": qid, "labels": {"de": {"value": label}}, "claims": claims}


@pytest.fixture
def ctx_for(make_ctx: Callable[..., Ctx]) -> Callable[..., Ctx]:
    def build(web: SiteWeb | None = None) -> Ctx:
        return make_ctx(web or SiteWeb(), budget=Budget(max_requests=300))

    return build


def _company(
    engine: Engine,
    name: str = "Muster Folien GmbH",
    *,
    city: str | None = "Berlin",
    plz: str | None = "12345",
    register: str | None = "HRB 99999",
) -> int:
    repo.sync_sources(engine, (), (), False)
    from market_intel.sources.user_data import store_document

    doc = store_document(
        engine,
        "user_upload",
        f"Stammdaten {name}".encode(),
        url=f"upload:{name}",
        title=name,
        publisher=None,
        published=None,
        content_type="text/plain",
    )
    with engine.begin() as conn:
        cid = repo.create_company(conn, name, country_code="DE", legal_name=name)
        if city or plz:
            repo.add_location(
                conn,
                cid,
                "registered_office",
                country_code="DE",
                city=city,
                postal_code=plz,
                street="Hauptstraße 5",
                document_id=doc,
                provenance=ProvenanceClass.OFFICIAL,
            )
        if register:
            repo.add_identifier(conn, cid, "gleif_ra", f"RA000001:{register}", doc)
    return cid


def _by_domain(engine: Engine, cid: int) -> dict[str, dict[str, Any]]:
    return {c["domain"]: c for c in websites.candidates_of(engine, cid)}


# ---------------------------------------------------------------------- Kandidaten und Prüfung


def test_official_site_found_and_scored_with_evidence(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine)
    out = websites.discover(ctx_for(), cid)
    cands = _by_domain(engine, cid)
    best = cands["muster-folien.de"]
    assert best["verdict"] == "eindeutig" and best["score"] >= 70
    pro = " ".join(best["details"]["pro"])
    assert "vollständiger Firmenname" in pro and "HRB99999" in pro and "PLZ 12345" in pro
    assert best["status"] == "vorgeschlagen"  # nie automatisch bestätigt
    assert {s["source"] for s in best["sources"]} >= {"wikidata"}
    assert best["document_ids"]  # Belege (Startseite, Impressum) gespeichert
    # Wikidata-Treffer gleichen Namens in Österreich wurde verworfen (anderes Land)
    assert "muster-folien.at" not in cands
    assert out["sources"]["wikidata"]["status"] == "ok"
    with engine.connect() as conn:
        assert repo.company_row(conn, cid)["website_domain"] is None


def test_lookalike_and_redirect_are_not_proposed(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine)
    websites.discover(ctx_for(), cid)
    cands = _by_domain(engine, cid)
    # musterfolien.de: abgeleitet, andere Firma in München → nicht vorgeschlagen
    assert "musterfolien.de" not in cands
    # muster-folien.com leitet auf eine fremde Firma weiter → nicht vorgeschlagen
    assert "ganz-andere.de" not in cands and "muster-folien.com" not in cands


def test_name_variants_match_via_alias(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine, name="MUSTER FOLIEN GMBH")  # andere Schreibweise
    websites.discover(ctx_for(), cid)
    assert _by_domain(engine, cid)["muster-folien.de"]["verdict"] == "eindeutig"


def test_other_location_contradicts(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine, city="Leipzig", plz="04109", register=None)
    websites.discover(ctx_for(), cid)
    cand = _by_domain(engine, cid)["muster-folien.de"]
    assert cand["verdict"] == "widerspricht"
    assert any("andere PLZ" in c for c in cand["details"]["contra"])


def test_missing_data_never_counts_as_match(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine, city=None, plz=None, register=None)
    websites.discover(ctx_for(), cid)
    cand = _by_domain(engine, cid)["muster-folien.de"]
    unchecked = " ".join(cand["details"]["unchecked"])
    assert "kein Standort belegt" in unchecked and "keine Registernummer belegt" in unchecked
    assert cand["verdict"] != "eindeutig"  # ohne Adresse/Register keine eindeutige Zuordnung
    checks = {c["criterion"]: c for c in cand["details"]["checks"]}
    assert checks["Standort"]["points"] == 0 and checks["Registernummer"]["points"] == 0


def test_brand_parent_site_is_flagged_not_trusted(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine)
    with engine.begin() as conn:  # Kontakt-E-Mail @muster-group.com aus einer Ausschreibung
        repo.add_identifier(conn, cid, "email_domain", "muster-group.com", None)
    websites.discover(ctx_for(), cid)
    group = _by_domain(engine, cid)["muster-group.com"]
    assert group["verdict"] == "widerspricht"  # andere Registernummer, andere PLZ
    contra = " ".join(group["details"]["contra"])
    assert "Registernummer" in contra and "PLZ" in contra


def test_duplicate_proposals_merge_sources(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine)
    with engine.begin() as conn:
        repo.add_identifier(conn, cid, "email_domain", "muster-folien.de", None)
    websites.discover(ctx_for(), cid)
    websites.discover(ctx_for(), cid)  # zweiter Lauf legt nichts doppelt an
    rows = [c for c in websites.candidates_of(engine, cid) if c["domain"] == "muster-folien.de"]
    assert len(rows) == 1
    assert {s["source"] for s in rows[0]["sources"]} == {"wikidata", "ted"}
    pro = " ".join(rows[0]["details"]["pro"])
    assert "E-Mail-Domain" in pro


def test_no_results_are_reported_honestly(engine: Engine, ctx_for: Any) -> None:
    cid = _company(engine, name="Gibtsnicht Spezial KG", register=None)
    out = websites.discover(ctx_for(), cid)
    assert websites.candidates_of(engine, cid) == []
    assert out["sources"]["wikidata"]["status"] == "keine"
    assert websites.website_state(engine, [cid])[cid] == "keine"
    assert websites.last_search(engine, cid)["outcome"]["stored"] == []


@pytest.mark.parametrize("mode", ["down", "429"])
def test_unreachable_or_rate_limited_source_is_logged_and_search_continues(
    engine: Engine, ctx_for: Any, mode: str
) -> None:
    cid = _company(engine)
    out = websites.discover(ctx_for(SiteWeb(wikidata=mode)), cid)
    assert out["sources"]["wikidata"]["status"] == "fehler"
    assert out["sources"]["wikidata"]["error"]
    # Ableitung + Prüfung laufen weiter: die echte Seite wird dennoch gefunden und belegt
    cand = _by_domain(engine, cid)["muster-folien.de"]
    assert {s["source"] for s in cand["sources"]} == {"ableitung"}
    assert cand["score"] > 0 and any(
        "abgeleitet" in u or "nur aus dem Namen" in u for u in cand["details"]["unchecked"]
    )


# ---------------------------------------------------------------------- Entscheidungen


def test_confirm_reject_and_no_resurfacing(engine: Engine, ctx_for: Any, tmp_path: Path) -> None:
    cid = _company(engine)
    with engine.begin() as conn:
        repo.add_identifier(conn, cid, "email_domain", "muster-group.com", None)
    websites.discover(ctx_for(), cid)
    cands = _by_domain(engine, cid)
    with pytest.raises(websites.WebsiteError, match="Autor"):
        websites.confirm(engine, cands["muster-folien.de"]["id"], " ")
    websites.reject(engine, cands["muster-group.com"]["id"], "Vertrieb", "Konzernseite")
    websites.discover(ctx_for(), cid)  # abgelehnt + gleiche Suchgrundlage → nicht wieder offen
    assert _by_domain(engine, cid)["muster-group.com"]["status"] == "abgelehnt"
    assert "muster-group.com" in websites.last_search(engine, cid)["outcome"]["skipped_rejected"]
    websites.confirm(engine, cands["muster-folien.de"]["id"], "Vertrieb")
    # Persistenz nach Neustart: neue Engine auf derselben Datei sieht alle Entscheidungen
    again = {c["domain"]: c for c in websites.candidates_of(db.make_engine(str(engine.url)), cid)}
    assert again["muster-group.com"]["status"] == "abgelehnt"
    assert again["muster-group.com"]["decision_reason"] == "Konzernseite"
    assert again["muster-folien.de"]["status"] == "bestaetigt"
    with engine.connect() as conn:
        row = repo.company_row(conn, cid)
        edits = conn.execute(
            select(db.company_edit.c.field, db.company_edit.c.new_value).where(
                db.company_edit.c.company_id == cid
            )
        ).all()
    assert (row["website_domain"], row["website_source"]) == ("muster-folien.de", "bestaetigt")
    assert ("website_domain", "muster-folien.de") in [tuple(e) for e in edits]
    assert websites.website_state(engine, [cid])[cid] == "bestaetigt"
    out = websites.discover(ctx_for(), cid)  # bestätigt → keine neue Suche
    assert out["status"] == "bestätigt"
    # neue Suchgrundlage (neuer Standort) → abgelehnter Vorschlag darf wieder geprüft werden
    websites.reject(engine, cands["muster-folien.de"]["id"], "Vertrieb", "doch falsch")
    with engine.connect() as conn:
        assert repo.company_row(conn, cid)["website_domain"] is None
    with engine.begin() as conn:
        repo.add_location(
            conn,
            cid,
            "branch",
            country_code="DE",
            city="Potsdam",
            postal_code="14467",
            street=None,
            document_id=_doc(engine),
            provenance=ProvenanceClass.OFFICIAL,
        )
    websites.discover(ctx_for(), cid)
    assert _by_domain(engine, cid)["muster-folien.de"]["status"] == "vorgeschlagen"
    # neue Grundlage: auch der früher abgelehnte Konzernvorschlag wird wieder geprüft
    assert _by_domain(engine, cid)["muster-group.com"]["status"] == "vorgeschlagen"


def _doc(engine: Engine) -> int:
    from market_intel.sources.user_data import store_document

    return store_document(
        engine,
        "user_upload",
        b"neuer Standort",
        url="upload:standort",
        title="Standort",
        publisher=None,
        published=None,
        content_type="text/plain",
    )


def test_bulk_confirm_only_unique_clear(engine: Engine, ctx_for: Any) -> None:
    a = _company(engine)
    b = _company(engine, name="Muster Folien GmbH", city="Leipzig", plz="04109", register=None)
    websites.discover(ctx_for(), a)
    websites.discover(ctx_for(), b)
    ids = [c["id"] for c in websites.pending(engine)]
    done = websites.confirm_clear(engine, ids, "Vertrieb")
    assert done == [a]  # nur der eindeutige Vorschlag; b widerspricht → bleibt offen
    assert websites.website_state(engine, [a, b]) == {a: "bestaetigt", b: "pruefen"}


def test_ted_contact_only_for_single_unambiguous_tenderer() -> None:
    single = ted.parse_notice(
        {
            "publication-number": "5081-2025",
            "notice-title": {"deu": "Beschriftung"},
            "notice-type": "can-standard",
            "winner-name": {"deu": ["Heerlein Werbetechnik GmbH & Co. KG"]},
            "organisation-name-tenderer": {"deu": ["Heerlein Werbetechnik GmbH & Co. KG"]},
            "organisation-email-tenderer": ["office@heerlein-werbetechnik.de"],
            "organisation-tel-tenderer": ["+49302163407"],
            "organisation-post-code-tenderer": ["10249"],
            "organisation-city-tenderer": ["Berlin"],
            "organisation-country-tenderer": ["DEU"],
        }
    )
    assert single is not None and single.contact["email"] == "office@heerlein-werbetechnik.de"
    assert ted.email_domain(single.contact["email"]) == "heerlein-werbetechnik.de"
    assert ted.email_domain("info@gmail.com") is None  # Freemail sagt nichts über die Website
    assert ted.phone_digits("+49 30 2163407") == "49302163407"
    two = ted.parse_notice(
        {
            "publication-number": "1-2025",
            "notice-title": {"deu": "x"},
            "notice-type": "can-standard",
            "winner-name": {"deu": ["A GmbH"]},
            "organisation-name-tenderer": {"deu": ["A GmbH", "B GmbH"]},
            "organisation-email-tenderer": ["a@a.de", "b@b.de"],
        }
    )
    assert two is not None and two.contact == {}  # mehrere Bieter: nicht zuzuordnen


# ---------------------------------------------------------------------- Analyse und Bewertung


def test_confirmation_triggers_analysis_and_consistent_reassessment(
    world: dict[str, Any], engine: Engine, make_ctx: Callable[..., Ctx], tmp_path: Path
) -> None:
    from market_intel.config import Settings
    from market_intel.opportunity.current import freshness

    cid = world["ids"]["glas"]
    with engine.begin() as conn:  # Website „vergessen“, um sie per Ermittlung zu bestätigen
        domain = repo.company_row(conn, cid)["website_domain"]
        conn.execute(
            db.company.update()
            .where(db.company.c.id == cid)
            .values(website_domain=None, website_source=None)
        )
    assert domain
    ctx_rt = (world["cases"], world["catalog"], world["model"])
    before = latest_assessments(engine, cid)[0]
    now = db.utcnow()
    with engine.begin() as conn:  # Vorschlag wie aus der Ermittlung
        conn.execute(
            db.website_candidate.insert().values(
                company_id=cid,
                domain=domain,
                url=f"https://{domain}/",
                status="vorgeschlagen",
                score=80,
                verdict="eindeutig",
                sources="[]",
                details="{}",
                document_ids="[]",
                search_basis="x",
                found_at=now,
                checked_at=now,
            )
        )
        cand_id = conn.execute(select(db.website_candidate.c.id)).scalar()
    websites.confirm(engine, int(cand_id), "Vertrieb")
    assert not freshness(engine, *ctx_rt)[cid].current  # neue Website → Bewertung veraltet
    job = create_company_job(engine, "analyze", [cid])
    runtime = Runtime(
        Settings(database_url=str(engine.url), output_dir=tmp_path),
        *ctx_rt,
        ctx_factory=lambda budget, _n: make_ctx(FakeWeb(), budget=budget),
    )
    assert Worker(engine, runtime).run_once() == job
    assert get_job(engine, job)["status"] == "completed"  # type: ignore[index]
    after = latest_assessments(engine, cid)[0]
    assert json.loads(after["details"])["trigger"] == "website-analyse"
    assert freshness(engine, *ctx_rt)[cid].current
    details = json.loads(after["details"])
    assert details["assessment"]["data_status"]["key"] == "eigene_angaben"
    lead = next(
        r
        for r in lead_rows(engine, include_no_fit=True, include_excluded=True)
        if r["firma_id"] == cid
    )
    assert lead["website_status"] == "Website bestätigt" and lead["aktualitaet"] == "aktuell"
    assert lead["prioritaet"] == after["priority_score"]
    export_leads(engine, tmp_path, include_no_fit=True, include_excluded=True)
    exported = {
        r["firma_id"]: r for r in json.loads((tmp_path / "leads.json").read_text())["leads"]
    }
    assert exported[cid]["website_status"] == "Website bestätigt"
    assert int(before["id"]) < int(after["id"])


def test_company_without_website_is_not_penalised(world: dict[str, Any], engine: Engine) -> None:
    """Ohne Website: „keine Daten / manuelle Prüfung“, nie „kein Bedarf“."""
    from market_intel.opportunity.run import assess_and_store

    with engine.begin() as conn:
        cid = repo.create_company(conn, "Ohne Web Folierung GmbH", country_code="DE")
    result = assess_and_store(engine, [cid], world["cases"], world["catalog"], world["model"])[cid]
    assert result["assessment"]["fit_status"] == "keine_daten"
    assert "unbekannt" in result["assessment"]["fit_status_label"]


# ---------------------------------------------------------------------- Rechercheauftrag und Oberfläche


def test_research_run_includes_website_discovery(run_research: Any, engine: Engine) -> None:
    from .test_end_to_end import PROOF_REQUEST

    _ctx, run_id, status = run_research(PROOF_REQUEST)
    assert status in ("completed", "completed_with_gaps")
    with engine.connect() as conn:
        tasks = conn.execute(
            select(db.research_task.c.status, db.research_task.c.params).where(
                db.research_task.c.run_id == run_id, db.research_task.c.task_type == "find_website"
            )
        ).all()
    assert tasks and all(t.status in ("succeeded", "skipped") for t in tasks)
    with engine.connect() as conn:
        searched = conn.execute(
            select(db.website_search.c.company_id).where(
                db.website_search.c.research_run_id == run_id
            )
        ).all()
    assert searched  # Suchprotokoll je Firma ohne bestätigte Website


def test_ui_review_confirm_in_browser(engine: Engine, ctx_for: Any, cases: dict[str, Any]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    from market_intel.ui.server import make_server

    cid = _company(engine)
    other = _company(engine, city="Leipzig", plz="04109", register=None)
    websites.discover(ctx_for(), cid)
    websites.discover(ctx_for(), other)
    server = make_server(engine, "127.0.0.1", 0, (cases, load_products(), load_model()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"Chromium nicht installiert: {exc}")
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/websites")
            card = page.locator("article.candidate", has_text="eindeutige Zuordnung")
            assert "eindeutige Zuordnung" in (card.text_content() or "")
            assert "vollständiger Firmenname" in (card.text_content() or "")
            card.locator("form[action$='/confirm'] input[name=author]").fill("Vertrieb")
            with page.expect_navigation():
                card.locator("button:has-text('Diese Website bestätigen')").click()
            assert "Website bestätigt" in page.content()
            page.goto(f"{base}/company/{cid}#websites")
            assert "Website bestätigt" in (page.text_content("#websites") or "")
            # Bearbeitername wurde gemerkt
            assert page.locator("#websites input[name=author]").first.input_value() == "Vertrieb"
            # Ablehnen im Browser (zweiter Knopf desselben Formulars)
            page.goto(f"{base}/company/{other}#websites")
            card = page.locator("#websites article.candidate").first
            assert "spricht dagegen" in (card.text_content() or "")
            with page.expect_navigation():
                card.locator("button:has-text('Ablehnen')").click()
            assert "Vorschlag abgelehnt" in page.content()
            browser.close()
            assert errors == []
    finally:
        server.shutdown()
        server.server_close()
    assert {c["status"] for c in websites.candidates_of(engine, other)} == {"abgelehnt"}
    with engine.connect() as conn:
        assert repo.company_row(conn, cid)["website_source"] == "bestaetigt"
        jobs_rows = conn.execute(select(db.job.c.kind)).scalars().all()
    assert "analyze" in jobs_rows  # Analyse und Neubewertung wurde eingereiht


def test_ui_reject_and_search_routes(engine: Engine, ctx_for: Any, cases: dict[str, Any]) -> None:
    from market_intel.ui.server import make_server

    cid = _company(engine)
    websites.discover(ctx_for(), cid)
    server = make_server(engine, "127.0.0.1", 0, (cases, load_products(), load_model()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/company/{cid}").read().decode()
        assert "Website-Ermittlung" in page and "muster-folien.de" in page
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        cand = _by_domain(engine, cid)["muster-folien.de"]["id"]
        data = urllib.parse.urlencode({"csrf": token, "author": "", "back": "/websites"}).encode()
        body = urllib.request.urlopen(f"{base}/websites/{cand}/reject", data=data).read().decode()
        assert "Autor" in body  # Fehler wird angezeigt, nichts geändert
        data = urllib.parse.urlencode({"csrf": token, "author": "V", "back": "/websites"}).encode()
        urllib.request.urlopen(f"{base}/websites/{cand}/reject", data=data).read()
        assert _by_domain(engine, cid)["muster-folien.de"]["status"] == "abgelehnt"
        data = urllib.parse.urlencode({"csrf": token}).encode()
        target = urllib.request.urlopen(f"{base}/company/{cid}/websites/search", data=data).geturl()
        assert "/jobs/" in target
    finally:
        server.shutdown()
        server.server_close()


def test_guessed_domains_cost_one_attempt_and_do_not_count_as_source_failures(
    engine: Engine, ctx_for: Any
) -> None:
    """E2E 2026-11-12: nicht existierende abgeleitete Domains erzeugten ~96 Fehlabrufe."""
    cid = _company(engine, name="Gibtsnicht Spezial KG", register=None)
    ctx = ctx_for()
    websites.discover(ctx, cid)
    with engine.connect() as conn:
        rows = conn.execute(
            select(db.fetch_log.c.url, db.fetch_log.c.attempt, db.fetch_log.c.purpose).where(
                db.fetch_log.c.source_key == "website"
            )
        ).all()
    derived = websites.derived_domains(websites.facts_of(engine, cid))
    assert derived and rows
    assert all(r.attempt == 1 for r in rows)  # kein Wiederholen bei vermuteten Domains
    assert all("Website-Ermittlung" in r.purpose for r in rows)  # getrennt zählbar
    assert len(rows) <= 4 * len(derived)  # robots + Seite, je mit und ohne www (vorher ~12)


def test_unreachable_memo_only_for_company_websites(make_ctx: Callable[..., Ctx]) -> None:
    web = SiteWeb(failing_hosts=frozenset({"tot.example.de", "api.gleif.org"}))
    ctx = make_ctx(web, budget=Budget(max_requests=100))
    first = ctx.gateway.get("website", "https://tot.example.de/", purpose="t")
    calls = web.calls["tot.example.de"]
    second = ctx.gateway.get("website", "https://tot.example.de/kontakt", purpose="t")
    assert first.status.value == second.status.value == "network_error"
    assert web.calls["tot.example.de"] == calls  # kein erneuter Verbindungsversuch
    ctx.gateway.get("gleif", "https://api.gleif.org/api/v1/lei-records/X", purpose="t")
    gleif_calls = web.calls["api.gleif.org"]
    ctx.gateway.get("gleif", "https://api.gleif.org/api/v1/lei-records/Y", purpose="t")
    assert web.calls["api.gleif.org"] > gleif_calls  # Datenquellen: weiter versucht
