# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
"""Phase F: Datensicherung, Wettbewerbsansicht, Dubletten in der Anzeige, Navigation."""

from __future__ import annotations

import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from market_intel import db, repo
from market_intel.backup import (
    KEEP,
    BackupError,
    backup_if_due,
    create_backup,
    list_backups,
    restore_backup,
)
from market_intel.domain import EvidenceDraft, ProvenanceClass
from market_intel.intel.competitors import analyze_competitors
from market_intel.intel.profile import build_profile
from market_intel.opportunity.leads import lead_rows
from market_intel.sources.user_data import store_document

from .test_opportunity import world  # noqa: F401  (Fixture)


# ---------------------------------------------------------------------- Datensicherung


def test_backup_is_consistent_checked_and_pruned(engine: Engine, tmp_path: Path) -> None:
    with engine.begin() as conn:
        repo.create_company(conn, "Gesichert GmbH")
    folder = tmp_path / "backups"
    info = create_backup(engine, folder)
    assert info.companies == 1 and info.path.exists()
    con = sqlite3.connect(info.path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    assert backup_if_due(engine, folder) is None  # heute schon gesichert
    for _ in range(KEEP + 2):
        create_backup(engine, folder)
    assert len(list_backups(folder)) == KEEP  # ältere entfernt


def test_restore_brings_back_old_state_and_saves_current(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    with engine.begin() as conn:
        repo.create_company(conn, "Alt GmbH")
    old = create_backup(engine, folder)
    with engine.begin() as conn:
        repo.create_company(conn, "Neu GmbH")
    database = Path(str(engine.url.database))
    engine.dispose()
    safety = restore_backup(old.path, database, folder)
    assert safety is not None and safety.companies == 2  # aktueller Stand vorher gesichert
    con = sqlite3.connect(database)
    names = [r[0] for r in con.execute("SELECT display_name FROM company")]
    con.close()
    assert names == ["Alt GmbH"]


def test_restore_rejects_broken_file(engine: Engine, tmp_path: Path) -> None:
    broken = tmp_path / "kaputt.db"
    broken.write_bytes(b"keine datenbank" * 100)
    database = Path(str(engine.url.database))
    before = database.read_bytes()
    with pytest.raises(BackupError):
        restore_backup(broken, database, tmp_path / "backups")
    assert database.read_bytes() == before  # nichts überschrieben


def test_cli_backup(engine: Engine, capsys: Any) -> None:
    from market_intel.cli import main

    url = str(engine.url)
    assert main(["--db", url, "backup", "create"]) == 0
    assert "geprüft" in capsys.readouterr().out
    assert main(["--db", url, "backup", "list"]) == 0
    assert "market_intel-" in capsys.readouterr().out


# ---------------------------------------------------------------------- Wettbewerb


def test_competitors_skip_public_buyers_and_store_only_related(
    world: dict[str, Any], engine: Engine
) -> None:
    with engine.begin() as conn:
        for i in range(5):
            repo.create_company(conn, f"Stadt {i}", entity_kind="public_buyer")
    cid = world["ids"]["folierer"]
    with engine.begin() as conn:
        result = analyze_competitors(conn, cid, store=True)
        stored = conn.execute(
            select(db.competitor_assessment.c.classification).where(
                db.competitor_assessment.c.company_id == cid
            )
        ).scalars().all()
    names = {r["name"] for g in result["groups"].values() for r in g}
    assert not any(n.startswith("Stadt ") for n in names)
    assert result["compared"] == len(world["ids"]) - 1
    assert set(stored) <= {"direct", "indirect", "related_market"}


# ---------------------------------------------------------------------- Dubletten-Anzeige


def test_signal_shown_once_even_if_stored_per_use_case_or_as_event(engine: Engine) -> None:
    repo.sync_sources(engine, (), (), False)
    doc = store_document(
        engine, "user_upload", b"Pressemitteilung", url="upload:presse.txt", title="Presse",
        publisher=None, published=date(2026, 3, 1), content_type="text/plain",
    )  # fmt: skip
    with engine.begin() as conn:
        cid = repo.create_company(conn, "Doppel GmbH")
        evidence = EvidenceDraft(doc, "upload:presse.txt", "Neue Halle eröffnet", "x", "1", True)
        for use_case in ("fahrzeugfolierung", "glasfolien"):
            repo.add_signal(
                conn, company_id=cid, signal_type="tender_published", event_date=None,
                summary="Ausschreibung: Folierung", country_code="DE", use_case_key=use_case,
                evidence=evidence, url="https://ted.test/1", provenance=ProvenanceClass.OFFICIAL,
                confidence="medium", run_id=None,
            )  # fmt: skip
        repo.add_signal(
            conn, company_id=cid, signal_type="expansion", event_date=None,
            summary="Neue Halle eröffnet", country_code=None, use_case_key=None,
            evidence=evidence, url="upload:presse.txt",
            provenance=ProvenanceClass.COMPANY_REPORTED, confidence="low", run_id=None,
        )  # fmt: skip
        repo.add_event(
            conn,
            repo.EventDraft(
                company_id=cid, event_type="new_site", summary="Neue Halle eröffnet",
                evidence=evidence, provenance=ProvenanceClass.COMPANY_REPORTED,
                confidence="low", method="regex_rule", event_date=None,
                date_precision="unknown", affected_headcount=None,
                verification_status="unverified",
            ),
            None,
        )  # fmt: skip
        profile = build_profile(conn, cid)
    signals = profile["history"]["signals"]
    assert len(signals) == 1  # Ausschreibung einmal, Pressemeldung nur als Ereignis
    assert signals[0]["use_cases"] == ["fahrzeugfolierung", "glasfolien"]
    texts = [g["text"] for g in profile["overview"]["growth_indicators"]]
    assert sum("Neue Halle eröffnet" in t for t in texts) == 1
    assert all("Datum unbekannt:" not in t for t in texts)
    assert any(t.startswith("veröffentlicht 2026-03-01") for t in texts)


def test_lead_figures_use_german_labels(world: dict[str, Any], engine: Engine) -> None:
    rows = lead_rows(engine, include_no_fit=True, include_excluded=True)
    figures = " ".join(r["mitarbeiter"] for r in rows)
    assert "company_reported" not in figures and "official" not in figures
    assert "unknown" not in figures


# ---------------------------------------------------------------------- Oberfläche


def test_pages_navigation_and_pagination(world: dict[str, Any], engine: Engine) -> None:
    from market_intel.ui.server import make_server

    with engine.begin() as conn:
        for i in range(60):
            repo.create_company(conn, f"Firma {i:02d} GmbH")
    server = make_server(
        engine, "127.0.0.1", 0, (world["cases"], world["catalog"], world["model"])
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for path, needle in (
            ("/", "Übersicht"),
            ("/companies", "Seite 1 von 2"),
            ("/companies?page=2", "Seite 2 von 2"),
            ("/companies?q=Firma%2005", "Firma 05 GmbH"),
            ("/customers", "Bestandskunden"),
            ("/products", "Produktmatrix"),
            ("/sources", "Quellen und Belege"),
            ("/settings", "Datensicherung"),
        ):
            page = urllib.request.urlopen(base + path).read().decode()
            assert needle in page, path
            assert 'class="mainnav"' in page and 'href="/jobs"' in page
        first = urllib.request.urlopen(base + "/companies").read().decode()
        assert first.count('<a href="/company/') == 50
        redirect = urllib.request.urlopen(base + "/?q=Firma").geturl()
        assert redirect.endswith("/companies?q=Firma")
        data = urllib.parse.urlencode(
            {"csrf": _token(urllib.request.urlopen(base + "/settings").read().decode())}
        ).encode()
        page = urllib.request.urlopen(base + "/settings/backup", data=data).read().decode()
        assert "Sicherung geprüft" in page
    finally:
        server.shutdown()
        server.server_close()


def _token(page: str) -> str:
    import re

    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match
    return match.group(1)

