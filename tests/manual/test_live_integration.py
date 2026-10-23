"""Manueller Integrationstest mit realen Unternehmen und öffentlichen Quellen.

Läuft NUR mit ``MI_LIVE=1`` und Internetzugang (Firewall-Freigabe der Quellen), z. B.:

    set MI_LIVE=1                                   (Windows)   bzw.  export MI_LIVE=1
    set MI_CONTACT_EMAIL=ihre.adresse@firma.de
    pytest tests/manual -m live -s

Geprüft wird nur, was öffentlich belegbar ist; konkrete Zahlen werden NICHT fest erwartet
(Quellen ändern sich), sondern Form, Herkunft und Nachvollziehbarkeit. Das Ergebnis
(Bericht + Abrufprotokoll) liegt danach in ``live-out/``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from market_intel import db, repo
from market_intel.config import Settings
from market_intel.engine.budget import Budget
from market_intel.engine.strategy import ResearchRequest
from market_intel.research import create_research, execute, named_companies
from market_intel.usecases import load_use_cases, register

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MI_LIVE") != "1",
                       reason="manueller Live-Test: MI_LIVE=1 setzen"),
]

OUT = Path("live-out")


def test_real_companies_end_to_end() -> None:
    from market_intel.cli import make_ctx

    OUT.mkdir(exist_ok=True)
    db_path = OUT / "live.db"
    settings = Settings(database_url=f"sqlite:///{db_path}",
                        contact_email=os.environ.get("MI_CONTACT_EMAIL", ""))
    assert settings.contact_email, "MI_CONTACT_EMAIL setzen (Pflicht für SEC/Wikimedia)"
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    cases = load_use_cases()
    register(engine, cases)
    budget = Budget(max_requests=400, max_candidates=6, max_seconds=1800)
    request = ResearchRequest(
        task="Vergleiche die Unternehmensentwicklung von „3M“ und „ORAFOL“ über die letzten "
             "fünf Jahre.", countries=["DE"], years=5)
    run_id = create_research(engine, request, budget, OUT)
    ctx = make_ctx(settings, engine, budget, no_llm=True)
    status = execute(ctx, run_id, cases)
    ctx.gateway.close()
    print(f"Status: {status}; Bericht: {OUT / 'bericht.md'}")

    named = named_companies(engine, run_id)
    assert len(named) >= 1, "kein genanntes Unternehmen gefunden – Abrufprotokoll prüfen"
    with engine.connect() as conn:
        failures = conn.execute(select(db.fetch_log.c.source_key, db.fetch_log.c.status,
                                       db.fetch_log.c.error).where(
            db.fetch_log.c.run_id == run_id,
            db.fetch_log.c.status.in_(["network_error", "http_error"]))).all()
        for f in failures[:20]:
            print("Quellenfehler:", f.source_key, f.status, (f.error or "")[:120])
        for company_id in named:
            ids = repo.identifiers_of(conn, company_id)
            if "lei" in ids:
                assert re.fullmatch(r"[A-Z0-9]{20}", ids["lei"]), ids["lei"]
            for kind in ("financial", "employee"):
                for row in repo.observations(conn, company_id, kind):
                    assert row["source_url"].startswith(("https://", "upload:"))
                    assert row["quote"] and row["retrieved_at"] is not None
        three_m = next((c for c in named if repo.identifiers_of(conn, c).get("sec_cik")), None)
    if three_m is not None:  # 3M ist SEC-meldepflichtig: mehrjährige amtliche Umsatzreihe
        with engine.connect() as conn:
            revenue = [r for r in repo.observations(conn, three_m, "financial", "revenue")
                       if r["provenance_class"] == "official"]
        assert len({r["period_end"].year for r in revenue}) >= 3
    report = (OUT / "bericht.md").read_text(encoding="utf-8")
    assert "## 7. Quellenverzeichnis" in report


# Phase 5: Ende-zu-Ende mit realen Unternehmen aus verschiedenen Branchen. Die Auswahl ist eine
# Testhypothese (Branche laut allgemeinem Wissen, NICHT als Ergebnis zu lesen); erwartet wird
# nur, dass jede Aussage belegt ist und das Negativbeispiel keinen hohen Fit bekommt.
LIVE_COMPANIES = {
    "ORAFOL": "Folienhersteller (technische Folien)",
    "Avery Dennison": "Folienhersteller",
    "Sixt": "Autovermietung (Entfolierung/Remarketing-Hypothese)",
    "Umdasch Group": "Ladenbau (Shopfitting)",
    "Steinel": "Werkzeug-/Gerätehersteller (Heißluft) – Wettbewerbs-/Partnerfall",
    "Deutsche Bank": "Negativbeispiel: Bank, kein Folienbezug erwartet",
}


def test_real_companies_opportunity_end_to_end() -> None:
    from market_intel.cli import make_ctx
    from market_intel.intel.manual import document_plain_text
    from market_intel.opportunity.model import load_model
    from market_intel.opportunity.run import assess_and_store
    from market_intel.products import load_products

    OUT.mkdir(exist_ok=True)
    settings = Settings(database_url=f"sqlite:///{OUT / 'live_opportunity.db'}",
                        contact_email=os.environ.get("MI_CONTACT_EMAIL", ""))
    assert settings.contact_email, "MI_CONTACT_EMAIL setzen (Pflicht für SEC/Wikimedia)"
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    cases = load_use_cases()
    register(engine, cases)
    budget = Budget(max_requests=900, max_candidates=len(LIVE_COMPANIES), max_seconds=3600)
    request = ResearchRequest(
        task="Analysiere " + ", ".join(f"„{n}“" for n in LIVE_COMPANIES),
        companies=list(LIVE_COMPANIES), use_cases=sorted(cases), years=5)
    run_id = create_research(engine, request, budget, OUT / "opportunity")
    ctx = make_ctx(settings, engine, budget, no_llm=True)
    status = execute(ctx, run_id, cases)
    ctx.gateway.close()
    named = named_companies(engine, run_id)
    print(f"Status {status}; aufgelöst: {len(named)}/{len(LIVE_COMPANIES)}")
    assert named, "keine Firma aufgelöst – Abrufprotokoll in live-out/ prüfen"
    best = assess_and_store(engine, named, cases, load_products(), load_model(), run_id)
    for result in best.values():
        scores = result["scores"]
        print(f"- {result['company']}: {result['segment']['name']} · Fit "
              f"{scores['product_relevance']} · Priorität {scores['priority']}")
        hyp = result["hypothesis"]
        for item in hyp["activity_evidence"] + hyp["process_evidence"] + hyp["counter_evidence"]:
            text = " ".join(document_plain_text(engine, item["document_id"]).split())
            assert " ".join(item["quote"].split()) in text  # jedes Zitat steht im Dokument
            print(f"    {'GEGEN' if item in hyp['counter_evidence'] else 'PRO'}: "
                  f"„{item['quote'][:120]}“ – {item['url']}")
        if result["company"].lower().startswith("deutsche bank"):
            assert not result["segment"]["key"].startswith("fit_hoch"), "Negativfall mit hohem Fit"
