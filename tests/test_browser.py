# ruff: noqa: F811  (Fixture ``world`` wird aus test_opportunity geteilt)
"""Echte Browser-Tests (Chromium über Playwright) für alle schreibenden Formulare.

Audit P0-1: Mit ``Referrer-Policy: no-referrer`` sendete Chromium bei Formularen
``Origin: null`` und jede Speicher-Aktion endete mit 403. Die bisherigen Tests nutzten urllib
ohne Origin-Header und konnten das nicht bemerken. Diese Tests klicken die Formulare im
echten Browser.

Wird übersprungen, wenn Playwright oder Chromium nicht installiert sind (``pip install
playwright`` und ``playwright install chromium``).
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine, select

from market_intel import db, repo
from market_intel.opportunity.outcomes import current_outcome
from market_intel.ui.server import REFERRER_POLICY, make_server, origin_allowed

from .test_opportunity import world  # noqa: F401  (Fixture)

sync_api = pytest.importorskip("playwright.sync_api")

ALLOWED = {"127.0.0.1:8765", "localhost:8765"}


# ---------------------------------------------------------------------- Regeln (ohne Browser)


@pytest.mark.parametrize(
    ("origin", "referer", "site", "expected"),
    [
        ("http://127.0.0.1:8765", None, "same-origin", True),  # Chromium/Edge mit Fix
        ("http://localhost:8765", None, None, True),  # älterer Browser ohne Fetch-Metadata
        ("null", None, "same-origin", True),  # null nur mit bestätigter gleicher Herkunft
        ("null", None, None, False),  # null ohne Beleg
        ("null", None, "cross-site", False),
        ("https://evil.test", None, None, False),
        ("http://evil.test", None, "same-origin", False),  # widersprüchlich → ablehnen
        ("https://127.0.0.1:8765", None, None, False),  # falsches Schema
        ("http://127.0.0.1:9999", None, None, False),  # falscher Port
        ("http://127.0.0.1:8765", None, "cross-site", False),
        ("http://127.0.0.1:8765", None, "same-site", False),  # andere Portanwendung
        (None, "http://127.0.0.1:8765/company/1", None, True),
        (None, "https://evil.test/x", None, False),
        (None, None, "cross-site", False),
        (None, None, None, True),  # Kommandozeile/Tests: CSRF-Token bleibt Pflicht
        ("  ", None, None, False),  # ungültiger Header
        ("garbage", None, None, False),
    ],
)
def test_origin_rules(
    origin: str | None, referer: str | None, site: str | None, expected: bool
) -> None:
    assert origin_allowed(origin, referer, site, ALLOWED) is expected


def test_referrer_policy_keeps_origin_for_forms() -> None:
    # no-referrer erzwingt „Origin: null“; same-origin sendet die echte Herkunft nur an sich
    # selbst und gibt externen Quellenlinks weiterhin keinen Referrer.
    assert REFERRER_POLICY == "same-origin"


# ---------------------------------------------------------------------- Browser


@pytest.fixture
def ui(world: dict[str, Any], engine: Engine) -> Iterator[str]:
    server = make_server(engine, "127.0.0.1", 0, (world["cases"], world["catalog"], world["model"]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def page(ui: str) -> Iterator[Any]:
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # pragma: no cover - Umgebung ohne Treiber
        pytest.skip(f"Playwright nicht startbar: {exc}")
    try:
        browser = pw.chromium.launch()
    except Exception as exc:  # pragma: no cover - Chromium fehlt
        pw.stop()
        pytest.skip(f"Chromium nicht installiert: {exc}")
    context = browser.new_context()
    pg = context.new_page()
    posts: list[tuple[int, str | None, str | None]] = []

    def on_response(r: Any) -> None:
        if r.request.method == "POST":
            sent = r.request.all_headers()  # tatsächlich gesendete Header inkl. Fetch-Metadata
            posts.append((r.status, sent.get("origin"), sent.get("sec-fetch-site")))

    pg.on("response", on_response)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.posts = posts  # type: ignore[attr-defined]
    yield pg
    assert errors == [], errors
    context.close()
    browser.close()
    pw.stop()


def _assert_last_post_ok(pg: Any, base: str) -> None:
    status, origin, site = pg.posts[-1]
    assert status == 303, pg.posts
    assert origin == base  # echte Herkunft statt „null“
    assert site == "same-origin"


def test_browser_saves_note(page: Any, ui: str, world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["folierer"]
    page.goto(f"{ui}/company/{cid}#notes")
    page.fill("form.noteform textarea[name=body]", "Browser-Notiz <b>fett</b>")
    page.fill("form.noteform input[name=author]", "Test")
    with page.expect_navigation():
        page.click("form.noteform button")
    _assert_last_post_ok(page, ui)
    assert "Browser-Notiz &lt;b&gt;fett&lt;/b&gt;" in page.content()
    with engine.connect() as conn:
        assert [n["body"] for n in repo.notes_of(conn, cid)] == ["Browser-Notiz <b>fett</b>"]
    # Entfernen ist ebenfalls ein Formular
    with page.expect_navigation():
        page.click(f"form[action^='/company/{cid}/notes/'] button")
    _assert_last_post_ok(page, ui)
    with engine.connect() as conn:
        assert repo.notes_of(conn, cid) == []


def test_browser_changes_watchlist(
    page: Any, ui: str, world: dict[str, Any], engine: Engine
) -> None:
    cid = world["ids"]["folierer"]
    page.goto(f"{ui}/company/{cid}/opportunity")
    page.select_option("form[action$='/watch'] select[name=status]", "in_pruefung")
    page.fill("#reason", "Browser-Test")
    page.fill("#author", "Test")
    with page.expect_navigation():
        page.click("form[action$='/watch'] button")
    _assert_last_post_ok(page, ui)
    with engine.connect() as conn:
        row = conn.execute(
            select(db.watchlist_entry.c.status, db.watchlist_entry.c.reason)
            .where(db.watchlist_entry.c.company_id == cid)
            .order_by(db.watchlist_entry.c.id.desc())
        ).first()
    assert row is not None and tuple(row) == ("in_pruefung", "Browser-Test")


def test_browser_records_outcome(page: Any, ui: str, world: dict[str, Any], engine: Engine) -> None:
    cid = world["ids"]["glas"]
    page.goto(f"{ui}/company/{cid}/opportunity")
    page.select_option("#outcome-status", "kontakt")
    page.fill("#outcome-date", date.today().isoformat())
    page.fill("#outcome-author", "Test")
    with page.expect_navigation():
        page.click("form[action$='/outcome'] button")
    _assert_last_post_ok(page, ui)
    assert current_outcome(engine, cid)["outcome"] == "kontakt"  # type: ignore[index]
    assert "Kontakt aufgenommen am" in page.content()


def test_browser_stores_competitor_assessment(
    page: Any,
    ui: str,
    world: dict[str, Any],
    engine: Engine,
) -> None:
    cid = world["ids"]["folierer"]
    with engine.connect() as conn:
        before = len(conn.execute(select(db.competitor_assessment.c.id)).all())
    page.goto(f"{ui}/company/{cid}/competitors")
    with page.expect_navigation():
        page.click("form[action$='/competitors/store'] button")
    _assert_last_post_ok(page, ui)
    with engine.connect() as conn:
        after = len(conn.execute(select(db.competitor_assessment.c.id)).all())
    assert after > before


def test_cross_origin_form_is_rejected_by_browser(
    page: Any,
    ui: str,
    world: dict[str, Any],
    engine: Engine,
) -> None:
    """Eine fremde Seite (anderer Port = andere Herkunft) sendet ein Formular mit gültigem
    Token – der Server muss trotzdem ablehnen."""
    cid = world["ids"]["folierer"]
    html = urllib.request.urlopen(f"{ui}/company/{cid}").read().decode()
    token = re.search(r'name="csrf" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]
    evil = make_server_evil(
        f'<form method="post" action="{ui}/company/{cid}/notes">'
        f'<input name="csrf" value="{token}"><input name="section" value="notes">'
        '<input name="author" value="X"><input name="body" value="fremd">'
        "<button>los</button></form>"
    )
    try:
        page.goto(evil)
        page.click("button")
        page.wait_for_load_state()
    finally:
        _stop_evil()
    status, origin, site = page.posts[-1]
    assert status == 403 and origin == evil.rstrip("/") and site in ("same-site", "cross-site")
    with engine.connect() as conn:
        assert repo.notes_of(conn, cid) == []


def test_post_without_token_still_rejected(ui: str, world: dict[str, Any]) -> None:
    cid = world["ids"]["folierer"]
    data = urllib.parse.urlencode({"section": "notes", "author": "A", "body": "x"}).encode()
    request = urllib.request.Request(
        f"{ui}/company/{cid}/notes",
        data=data,
        headers={"Origin": ui, "Sec-Fetch-Site": "same-origin"},
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(request)
    assert err.value.code == 403


# ---------------------------------------------------------------------- fremde Seite

_EVIL: dict[str, Any] = {}


def make_server_evil(body: str) -> str:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Evil(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            data = f"<!doctype html><html><body>{body}</body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Evil)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _EVIL["server"] = server
    return f"http://127.0.0.1:{server.server_address[1]}/"


def _stop_evil() -> None:
    server = _EVIL.pop("server")
    server.shutdown()
    server.server_close()
