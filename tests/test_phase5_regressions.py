"""Regressionstests aus dem Live-Test (Phase 5) und den geforderten Abnahmefällen."""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Engine, func, select

from market_intel import db, repo
from market_intel.config import Settings
from market_intel.domain import FetchStatus
from market_intel.engine.adapters import _gleif_named
from market_intel.engine.strategy import ResearchRequest
from market_intel.opportunity.evidence import Document, EvidenceIndex, legal_page
from market_intel.opportunity.leads import lead_rows
from market_intel.opportunity.model import load_model
from market_intel.opportunity.run import assess_and_store
from market_intel.opportunity.watch import set_status
from market_intel.products import load_products
from market_intel.sources.user_data import import_company_list

from .fakeweb import FakeWeb, _lei_record
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


# ---------------------------------------------------------------------- Live-Befunde 2026-10-23
# Die Rechtsnamen entsprechen den Live-Antworten der GLEIF-API (LEIs hier Testwerte).

REAL_NAMES = [
    _lei_record("TESTLEIORAFOLEU00001", "ORAFOL Europe GmbH", "DE", "Oranienburg"),
    _lei_record("TESTLEIORAFOLUS00001", "ORAFOL Americas Inc.", "US", "Ayer"),
    _lei_record("TESTLEI3MDE000000001", "3M Deutschland GmbH", "DE", "Neuss"),
    _lei_record("TESTLEI3MGREEN000001", "3M green energy GmbH", "DE", "Neuss"),
    _lei_record("TESTLEISIXTLEASING01", "Sixt Leasing SE", "DE", "Pullach"),
    _lei_record("TESTLEIUMDASCH000001", "Umdasch Group AG", "AT", "Amstetten"),
    _lei_record("TESTLEIAVERYNL000001", "Avery Dennison B.V.", "NL", "Hazerswoude"),
    _lei_record("TESTLEIAVERYRO000001", "Avery Dennison S.R.L", "RO", "Bukarest"),
    _lei_record("TESTLEIAVERYUS000001", "AVERY DENNISON CORPORATION", "US", "Mentor"),
    _lei_record("TESTLEIBETAONE000001", "Beta Druck GmbH", "DE", "Köln"),
    _lei_record("TESTLEIBETATWO000001", "Beta Druck AG", "AT", "Wien"),
]


class RealNamesWeb(FakeWeb):
    """GLEIF mit Rechtsnamen wie im Live-Test; Avery-Töchter melden die US-Mutter."""

    def _gleif(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        if path.endswith("/ultimate-parent"):
            if "TESTLEIAVERY" in path and "US" not in path:
                return httpx.Response(200, json={"data": REAL_NAMES[8]})
            return httpx.Response(404, json={"errors": [{"title": "Not Found"}]})
        needle = params.get("filter[entity.legalName]", "").lower()
        country = params.get("filter[entity.legalAddress.country]")
        found = [
            r
            for r in REAL_NAMES
            if needle in r["attributes"]["entity"]["legalName"]["name"].lower()
            and (not country or r["attributes"]["entity"]["legalAddress"]["country"] == country)
        ]
        return httpx.Response(200, json={"data": found})


def test_named_company_matches_legal_name_with_country_suffix(make_ctx: Any) -> None:
    """„ORAFOL“ → „ORAFOL Europe GmbH“ (vorher Ähnlichkeit 0,63 → nicht gefunden)."""
    ctx = make_ctx(RealNamesWeb())
    notes: list[str] = []
    record, doc_id = _gleif_named(ctx, "ORAFOL", "DE", notes)
    assert record is not None and record.legal_name == "ORAFOL Europe GmbH" and doc_id
    assert any("Länder-/Konzernzusatz" in n for n in notes)  # Abweichung offengelegt
    record, _ = _gleif_named(ctx, "3M", "DE", [])
    assert record is not None and record.legal_name == "3M Deutschland GmbH"  # nicht „green energy“


def test_named_company_activity_suffix_is_not_accepted(make_ctx: Any) -> None:
    """„Sixt“ darf nicht als „Sixt Leasing SE“ übernommen werden (andere Gesellschaft)."""
    notes: list[str] = []
    record, _ = _gleif_named(make_ctx(RealNamesWeb()), "Sixt", "DE", notes)
    assert record is None
    assert any("kein Rechtsname passt" in n for n in notes)


def test_named_company_with_foreign_seat_is_found(make_ctx: Any) -> None:
    """Auftrag mit Land DE, Umdasch Group AG sitzt in AT → weltweite Ausweichsuche."""
    notes: list[str] = []
    record, _ = _gleif_named(make_ctx(RealNamesWeb()), "Umdasch Group", "DE", notes)
    assert record is not None and record.country == "AT"
    assert any("Land DE" in n for n in notes)


def test_ambiguous_names_resolve_only_via_common_parent(make_ctx: Any) -> None:
    ctx = make_ctx(RealNamesWeb())
    notes: list[str] = []
    record, _ = _gleif_named(ctx, "Avery Dennison", None, notes)
    assert record is not None and record.legal_name == "AVERY DENNISON CORPORATION"
    assert any("oberste Konzernmutter" in n for n in notes)
    notes = []
    record, _ = _gleif_named(ctx, "Beta Druck", None, notes)
    assert record is None  # zwei gleichnamige Firmen ohne gemeinsame Mutter: nicht raten
    assert any("mehrdeutig" in n for n in notes)


class DeniedWeb(FakeWeb):
    """Ein Host verweigert den Zugriff mit Bot-Abwehr (wie yellotools.com im Live-Test)."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls[request.url.host] += 1
        if request.url.host == "www.wikidata.org":
            return httpx.Response(
                403, text="Please respect our robot policy", headers={"cf-mitigated": "challenge"}
            )
        return httpx.Response(200, json={"data": []})


def test_denied_host_is_not_contacted_again(make_ctx: Any, engine: Engine) -> None:
    """Live: Wikidata antwortete 403 (Robot-Policy) und wurde trotzdem weiter angefragt."""
    web = DeniedWeb()
    ctx = make_ctx(web)
    url = "https://www.wikidata.org/w/api.php"
    first = ctx.gateway.get("wikidata", url, params={"search": "a"}, purpose="t")
    second = ctx.gateway.get("wikidata", url, params={"search": "b"}, purpose="t")
    other = ctx.gateway.get("gleif", "https://api.gleif.org/api/v1/lei-records", purpose="t")
    assert first.http_status == 403 and not first.ok
    assert second.status is FetchStatus.BLOCKED_POLICY
    assert "Bot-Abwehr" in (second.error or "") and "keine weiteren Abrufe" in (second.error or "")
    assert web.calls["www.wikidata.org"] == 1  # zweite Anfrage nie gesendet
    assert other.ok  # andere Hosts unberührt
    with engine.connect() as conn:
        statuses = conn.execute(select(db.fetch_log.c.status)).scalars().all()
    assert statuses.count("blocked_policy") == 1  # Sperre ist protokolliert


def test_user_agent_follows_wikimedia_format() -> None:
    agent = Settings(contact_email="kontakt@example.org").user_agent
    assert re.fullmatch(
        r"YellotoolsMarketIntel/[\w.]+ \(mailto:kontakt@example\.org\) httpx/[\w.]+", agent
    )
    assert "Mozilla" not in agent  # keine Browser-Imitation


class ThrottledWeb(FakeWeb):
    """GDELT im Live-Test: jede Anfrage 429 (ohne Retry-After)."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls[request.url.host] += 1
        if request.url.host == "api.gdeltproject.org":
            return httpx.Response(429, text="Please limit requests to one every 5 seconds")
        return httpx.Response(200, json={"data": []})


def test_persistent_rate_limit_suspends_host(make_ctx: Any) -> None:
    web = ThrottledWeb()
    ctx = make_ctx(web)
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    results = [
        ctx.gateway.get("gdelt", url, params={"query": str(i)}, purpose="t") for i in range(4)
    ]
    assert [r.http_status for r in results[:2]] == [429, 429]
    assert all(r.status is FetchStatus.BLOCKED_POLICY for r in results[2:])
    assert "drosselt dauerhaft" in (results[2].error or "")
    assert web.calls["api.gdeltproject.org"] == 2 * ctx.settings.max_attempts  # danach Ruhe
    assert ctx.gateway.get("gleif", "https://api.gleif.org/api/v1/lei-records", purpose="t").ok


def test_listed_companies_take_precedence_over_discovery(
    run_research: Any, engine: Engine, tmp_path: Any
) -> None:
    """Live: Auftrag mit Firmenliste – 509 Suchtreffer verdrängten die sechs Listenfirmen."""
    repo.sync_sources(engine, (), (), False)
    path = tmp_path / "liste.csv"
    path.write_text("name;website\nListen-Firma GmbH;listen-firma.test\n", "utf-8")
    listed = import_company_list(
        engine, path, list_name="Prüfliste", source_url="intern:test", publisher=None
    ).companies
    request = ResearchRequest(
        task="Finde Werbetechnik-Betriebe in Deutschland",
        countries=["DE"],
        use_cases=["werbetechnik_beschriftung"],
        lists=["Prüfliste"],
        max_candidates=1,
    )
    _, run_id, _ = run_research(request)
    with engine.connect() as conn:
        found = conn.execute(
            select(db.run_candidate.c.company_id).where(db.run_candidate.c.run_id == run_id)
        ).scalars()
        selected = set(
            conn.execute(
                select(db.run_candidate.c.company_id).where(
                    db.run_candidate.c.run_id == run_id, db.run_candidate.c.selected.is_(True)
                )
            ).scalars()
        )
    assert len(set(found)) > 1  # die freie Suche hat weitere Kandidaten gefunden …
    assert selected == set(listed)  # … ausgewählt wird trotzdem die vorgegebene Liste


def _doc(doc_id: int, url: str) -> Document:
    return Document(doc_id, url, "website", datetime(2026, 10, 23, tzinfo=UTC), url)


def _index(pages: list[tuple[str, str]]) -> EvidenceIndex:
    index = EvidenceIndex.__new__(EvidenceIndex)  # ohne Datenbank: Texte direkt
    index.documents = [_doc(i, url) for i, (url, _) in enumerate(pages, 1)]
    index.texts = [(d, text) for d, (_, text) in zip(index.documents, pages, strict=True)]
    return index


def test_legal_notice_is_no_activity_evidence() -> None:
    """Live: Markenhinweis im STEINEL-Impressum ergab Fit 40 ohne Tätigkeitsbeleg."""
    impressum = (
        "Soweit nicht anders angegeben, sind alle Markenzeichen auf STEINEL Websites "
        "markenrechtlich geschützt. Dies gilt insbesondere für STEINEL Marken, Typenschilder, "
        "Firmenlogos und Embleme."
    )
    index = _index([("https://www.steinel.de/de/impressum/", impressum)])
    assert index.hits(("Typenschilder", "Embleme")) == []
    for url in ("https://www.orafol.com/en/legal-notice", "https://www.db.com/imprint?x=1"):
        assert legal_page(url)
    assert not legal_page("https://www.example.de/leistungen/fahrzeugfolierung")
    assert not legal_page("upload:impressum.pdf")  # eigene Uploads werden nicht gefiltert


def test_repeated_navigation_text_counts_once() -> None:
    """Live: „Banners“ aus dem ORAFOL-Menü erschien als sechs Belege (sechs Seiten)."""
    menu = "Products Banners Polycarbonate films Reflective solutions"
    pages = [(f"https://www.orafol.com/en/page{i}", menu + ". Mehr Text.") for i in range(6)]
    pages.append(("https://www.orafol.com/en/case", "Wir zeigen Banners für Messen."))
    hits = _index(pages).hits(("Banners",))
    assert len(hits) == 2  # Menüsatz einmal + der eigenständige Satz
    assert {h.document.document_id for h in hits} == {1, 7}
