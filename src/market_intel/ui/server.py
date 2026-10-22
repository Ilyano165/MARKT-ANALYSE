"""Lokale Weboberfläche (nur Standardbibliothek): ``mi ui``.

Sicherheit (SECURITY_AND_PRIVACY.md T10):

* bindet standardmäßig nur an 127.0.0.1; andere Adressen nur mit ``--allow-lan`` (ohne
  Anmeldung – nur in vertrauenswürdigen Netzen)
* Host-Header-Prüfung gegen DNS-Rebinding, CSRF-Token für jede Änderung, Origin-Prüfung
* Content-Security-Policy ohne Inline-Skripte, keine externen Ressourcen
* nur lesender Zugriff auf Quellen – die Oberfläche löst keine Netzabrufe aus
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Engine, func, or_, select

from .. import db, repo
from ..intel.competitors import analyze_competitors
from ..intel.manual import ManualEntryError, document_plain_text
from ..intel.profile import build_profile, observation_detail
from ..opportunity.leads import LEAD_COLUMNS, lead_rows
from ..opportunity.model import Model, load_model
from ..opportunity.report import opportunity_report
from ..opportunity.watch import STATUSES, set_status
from ..products import Catalog, load_products
from ..usecases import UseCase, load_use_cases
from . import render

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
STATIC = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
MAX_BODY = 64 * 1024
Discovery = tuple[dict[str, UseCase], Catalog, Model]


class App:
    """Zustand der Oberfläche (Engine, CSRF-Token, erlaubte Hosts)."""

    def __init__(
        self,
        engine: Engine,
        host: str,
        port: int,
        discovery: Discovery | None = None,
    ) -> None:
        self.engine = engine
        if discovery is None:
            discovery = (load_use_cases(), load_products(), load_model())
        self.cases: dict[str, UseCase] = discovery[0]
        self.catalog: Catalog = discovery[1]
        self.model: Model = discovery[2]
        self.csrf = secrets.token_urlsafe(32)
        names = {host, "127.0.0.1", "localhost"} if host in ("127.0.0.1", "localhost") else {host}
        self.allowed_hosts = {f"{n}:{port}" for n in names} | (
            {f"[::1]:{port}"} if host == "127.0.0.1" else set()
        )

    # ------------------------------------------------------------------ Seiten

    def companies(self, query: str) -> str:
        with self.engine.connect() as conn:
            stmt = select(db.company).where(db.company.c.merged_into_id.is_(None))
            if query:
                like = f"%{query.lower()}%"
                stmt = stmt.where(
                    or_(
                        func.lower(db.company.c.display_name).like(like),
                        func.lower(db.company.c.website_domain).like(like),
                    )
                )
            rows = [
                dict(r)
                for r in conn.execute(
                    stmt.order_by(db.company.c.display_name).limit(500)
                ).mappings()
            ]
            for row in rows:
                row["observations"] = sum(
                    conn.execute(
                        select(func.count()).select_from(t).where(t.c.company_id == row["id"])
                    ).scalar()
                    or 0
                    for t in db.OBSERVATION_TABLES.values()
                )
                row["locations"] = (
                    conn.execute(
                        select(func.count())
                        .select_from(db.company_location)
                        .where(db.company_location.c.company_id == row["id"])
                    ).scalar()
                    or 0
                )
        return render.page("Unternehmen", render.company_list(rows, query))

    def profile(self, company_id: int, years: int, estimates: bool) -> tuple[str, dict[str, Any]]:
        with self.engine.connect() as conn:
            data = build_profile(
                conn, company_id, years=years, include_estimates=estimates, as_of=date.today()
            )
        html = render.page(
            data["overview"]["display_name"],
            render.profile_page(data, csrf=self.csrf),
            data=data,
        )
        return html, data


def _exists(engine: Engine, company_id: int) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(select(db.company.c.id).where(db.company.c.id == company_id)).first()
            is not None
        )


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MarketIntel"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return  # keine Zugriffsprotokolle mit Suchbegriffen auf der Konsole

        # -------------------------------------------------------------- Antworten

        def _send(
            self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _html(self, html: str, status: int = 200) -> None:
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._html(
                render.page("Fehler", f"<h1>{status}</h1><p>{render.e(message)}</p>"), status
            )

        def _redirect(self, location: str) -> None:
            self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": location})

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in app.allowed_hosts

        # -------------------------------------------------------------- GET

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            if not self._host_ok():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "Unbekannter Host")
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            path = url.path
            try:
                self._route_get(path, query)
            except ManualEntryError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except Exception:  # Fehlerseite ohne Interna
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "Interner Fehler (Details im Terminal)"
                )
                raise

        def _route_get(self, path: str, query: dict[str, list[str]]) -> None:
            if path == "/":
                self._html(app.companies(query.get("q", [""])[0][:100]))
                return
            if path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
            if path in ("/leads", "/leads.csv"):
                segments = tuple(s for s in query.get("segment", []) if s)
                rows = lead_rows(
                    app.engine,
                    include_no_fit=query.get("nofit", [""])[0] == "1",
                    segments=segments,
                )
                if path.endswith(".csv"):
                    self._send(
                        200,
                        _csv(rows).encode("utf-8-sig"),
                        "text/csv; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="leads.csv"'},
                    )
                else:
                    self._html(
                        render.page(
                            "Leads",
                            render.leads_page(rows, app.model.segments, segments),
                        )
                    )
                return
            match = re.fullmatch(r"/company/(\d+)/opportunity", path)
            if match:
                company_id = int(match.group(1))
                if not _exists(app.engine, company_id):
                    self._error(404, "Unternehmen nicht gefunden")
                    return
                report = opportunity_report(
                    app.engine, company_id, app.cases, app.catalog, app.model
                )
                with app.engine.connect() as conn:
                    notes = [
                        n for n in repo.notes_of(conn, company_id) if n["section"] == "opportunity"
                    ]
                self._html(
                    render.page(
                        f"Opportunity {report['name']}",
                        render.opportunity_page(report, notes, app.csrf, STATUSES),
                    )
                )
                return
            if path.startswith("/static/"):
                name = path.removeprefix("/static/")
                if name in STATIC:
                    body = resources.files("market_intel.ui").joinpath("static", name).read_bytes()
                    self._send(200, body, STATIC[name])
                elif name == "world_map.json":
                    body = resources.files("market_intel.data").joinpath(name).read_bytes()
                    self._send(200, body, "application/json")
                else:
                    self._error(404, "nicht gefunden")
                return
            match = re.fullmatch(r"/company/(\d+)(\.json)?", path)
            if match:
                company_id = int(match.group(1))
                if not _exists(app.engine, company_id):
                    self._error(404, "Unternehmen nicht gefunden")
                    return
                years = _int(query.get("years", ["10"])[0], 10, 1, 15)
                estimates = query.get("est", [""])[0] == "1"
                html, data = app.profile(company_id, years, estimates)
                if match.group(2):
                    payload = json.dumps(data, ensure_ascii=False, default=str, indent=1)
                    self._send(200, payload.encode("utf-8"), "application/json; charset=utf-8")
                else:
                    self._html(html)
                return
            match = re.fullmatch(r"/company/(\d+)/competitors", path)
            if match:
                company_id = int(match.group(1))
                if not _exists(app.engine, company_id):
                    self._error(404, "Unternehmen nicht gefunden")
                    return
                with app.engine.connect() as conn:
                    result = analyze_competitors(conn, company_id)
                self._html(render.page("Wettbewerb", render.competitors_page(result, app.csrf)))
                return
            match = re.fullmatch(r"/observation/(financial|employee)/(\d+)", path)
            if match:
                with app.engine.connect() as conn:
                    detail = observation_detail(conn, match.group(1), int(match.group(2)))
                if detail is None:
                    self._error(404, "Beobachtung nicht gefunden")
                else:
                    self._html(render.page("Herkunft", render.observation_page(detail)))
                return
            match = re.fullmatch(r"/document/(\d+)", path)
            if match:
                document_id = int(match.group(1))
                with app.engine.connect() as conn:
                    doc = (
                        conn.execute(
                            select(
                                db.source_document.c.id,
                                db.source_document.c.url,
                                db.source_document.c.source_key,
                                db.source_document.c.title,
                                db.source_document.c.retrieved_at,
                                db.source_document.c.published_at,
                                db.source_document.c.content_type,
                                db.source_document.c.sha256,
                            ).where(db.source_document.c.id == document_id)
                        )
                        .mappings()
                        .first()
                    )
                if doc is None:
                    self._error(404, "Dokument nicht gefunden")
                    return
                text = document_plain_text(app.engine, document_id)
                excerpt = text[:20000] + ("\n…" if len(text) > 20000 else "")
                self._html(render.page("Dokument", render.document_page(dict(doc), excerpt)))
                return
            self._error(404, "nicht gefunden")

        # -------------------------------------------------------------- POST

        def do_POST(self) -> None:
            if not self._host_ok():
                self._error(HTTPStatus.MISDIRECTED_REQUEST, "Unbekannter Host")
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc not in app.allowed_hosts:
                self._error(HTTPStatus.FORBIDDEN, "Fremde Herkunft")
                return
            length = _int(self.headers.get("Content-Length", "0"), 0, 0, MAX_BODY + 1)
            if length > MAX_BODY:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Anfrage zu groß")
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            if not secrets.compare_digest(form.get("csrf", [""])[0], app.csrf):
                self._error(HTTPStatus.FORBIDDEN, "Sicherheitstoken fehlt oder ist abgelaufen")
                return
            path = urlsplit(self.path).path
            match = re.fullmatch(r"/company/(\d+)/notes", path)
            if match:
                company_id = int(match.group(1))
                section = form.get("section", ["notes"])[0]
                try:
                    with app.engine.begin() as conn:
                        repo.add_note(
                            conn,
                            company_id,
                            section,
                            form.get("body", [""])[0],
                            form.get("author", [""])[0],
                        )
                except ValueError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._redirect(f"/company/{company_id}#notes")
                return
            match = re.fullmatch(r"/company/(\d+)/notes/(\d+)/delete", path)
            if match:
                company_id, note_id = int(match.group(1)), int(match.group(2))
                with app.engine.begin() as conn:
                    note = (
                        conn.execute(
                            select(db.company_note).where(
                                db.company_note.c.id == note_id,
                                db.company_note.c.company_id == company_id,
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if note is None:
                        self._error(404, "Notiz nicht gefunden")
                        return
                    repo.add_note(
                        conn,
                        company_id,
                        note["section"],
                        "",
                        note["author"],
                        supersedes_id=note_id,
                        deleted=True,
                    )
                self._redirect(f"/company/{company_id}#notes")
                return
            match = re.fullmatch(r"/company/(\d+)/watch", path)
            if match:
                company_id = int(match.group(1))
                author = form.get("author", [""])[0]
                try:
                    set_status(
                        app.engine,
                        company_id,
                        form.get("status", ["vorgemerkt"])[0],
                        watchlist=form.get("list", ["Leads"])[0],
                        reason=form.get("reason", [""])[0],
                        author=author,
                    )
                    comment = form.get("comment", [""])[0].strip()
                    if comment:
                        with app.engine.begin() as conn:
                            repo.add_note(conn, company_id, "opportunity", comment, author)
                except ValueError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._redirect(f"/company/{company_id}/opportunity")
                return
            match = re.fullmatch(r"/company/(\d+)/competitors/store", path)
            if match:
                company_id = int(match.group(1))
                with app.engine.begin() as conn:
                    analyze_competitors(conn, company_id, store=True)
                self._redirect(f"/company/{company_id}#overview")
                return
            self._error(404, "nicht gefunden")

    return Handler


def _csv(rows: list[dict[str, Any]]) -> str:
    import csv
    import io

    from ..export import safe_cell

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(LEAD_COLUMNS), delimiter=";")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(row[k]) for k in LEAD_COLUMNS})
    return out.getvalue()


def _int(value: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except ValueError:
        return default


def make_server(
    engine: Engine,
    host: str = "127.0.0.1",
    port: int = 8765,
    discovery: Discovery | None = None,
) -> ThreadingHTTPServer:
    """Server erzeugen (Port 0 = frei wählen, für Tests).

    ``discovery`` = (Anwendungsfälle, Produktmatrix, Bewertungsmodell); Standard: mitgeliefert.
    """
    server = ThreadingHTTPServer((host, port), make_handler(App(engine, host, port, discovery)))
    if port == 0:
        actual = server.server_address[1]
        server.RequestHandlerClass = make_handler(App(engine, host, int(actual), discovery))
    return server


def export_profile_html(
    engine: Engine, company_id: int, years: int = 10, estimates: bool = False
) -> str:
    """Eigenständige HTML-Datei (CSS, JS, Karte eingebettet; Notizen nur lesend)."""
    with engine.connect() as conn:
        data = build_profile(conn, company_id, years=years, include_estimates=estimates)
    return render.page(
        data["overview"]["display_name"],
        render.profile_page(data, static=True),
        static=True,
        data=data,
    )
