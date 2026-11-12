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
import threading
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from sqlalchemy import Engine, func, or_, select

from .. import db, jobs, repo
from ..engine.budget import Budget
from ..engine.strategy import ResearchRequest
from ..intel.competitors import analyze_competitors
from ..intel.manual import ManualEntryError, document_plain_text
from ..intel.profile import build_profile, observation_detail
from ..opportunity.current import freshness, refresh, stale_ids
from ..opportunity.leads import LEAD_COLUMNS, lead_rows
from ..opportunity.model import Model, load_model
from ..opportunity.outcomes import record_outcome
from ..opportunity.report import opportunity_report
from ..opportunity.watch import STATUSES, set_status
from ..products import Catalog, load_products
from ..usecases import UseCase, load_use_cases
from . import render, views

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
STATIC = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "icon.png": "image/png",
}
MAX_BODY = 64 * 1024
MAX_UPLOAD = 10 * 1024 * 1024  # CSV-Import
REFERRER_POLICY = "same-origin"
SAME_ORIGIN_FETCH = frozenset({"same-origin", "none"})


def origin_allowed(
    origin: str | None,
    referer: str | None,
    fetch_site: str | None,
    allowed_hosts: set[str],
) -> bool:
    """Herkunftsprüfung für schreibende Anfragen (zusätzlich zum CSRF-Token).

    Reihenfolge der Belege:

    1. ``Sec-Fetch-Site`` (vom Browser gesetzt, von Seiten nicht fälschbar): nur
       ``same-origin`` oder ``none`` (direkte Nutzeraktion) sind erlaubt; ``same-site`` und
       ``cross-site`` werden abgelehnt.
    2. ``Origin``: muss ``http://<erlaubter Host>`` sein. ``null`` (Sandbox, Datei, manche
       Datenschutz-Einstellungen) gilt nur, wenn ``Sec-Fetch-Site`` die gleiche Herkunft
       bestätigt.
    3. Ohne ``Origin``: ``Referer`` muss, falls vorhanden, auf einen erlaubten Host zeigen.
    4. Ohne alle drei Angaben (Kommandozeilen-Clients, Tests): erlaubt – das CSRF-Token
       bleibt Pflicht.
    """
    if fetch_site is not None and fetch_site.strip().lower() not in SAME_ORIGIN_FETCH:
        return False
    if origin is not None:
        origin = origin.strip()
        if origin == "null":
            return fetch_site is not None
        parts = urlsplit(origin)
        return parts.scheme == "http" and parts.netloc in allowed_hosts and parts.path in ("", "/")
    if referer:
        parts = urlsplit(referer.strip())
        return parts.scheme == "http" and parts.netloc in allowed_hosts
    return True


Discovery = tuple[dict[str, UseCase], Catalog, Model]


class App:
    """Zustand der Oberfläche (Engine, CSRF-Token, erlaubte Hosts)."""

    def __init__(
        self,
        engine: Engine,
        host: str,
        port: int,
        discovery: Discovery | None = None,
        *,
        worker: Any = None,
        out_dir: Path | None = None,
        llm_available: bool = False,
        shell: Any = None,
        settings: Any = None,
        secret_store: Any = None,
    ) -> None:
        self.engine = engine
        self._secret_store = secret_store  # sicherer Schlüsselspeicher (Standard: System)
        self.settings = settings  # geladene Einstellungen (Einstellungsseite), optional
        self.shell = shell  # Desktop-Hülle (Fokus, Beenden) oder None
        self.worker = worker  # jobs.Worker oder None (Aufträge warten dann)
        self.out_dir = out_dir or Path("out")
        self.llm_available = llm_available
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

    @property
    def secret_store(self) -> Any:
        if self._secret_store is None:
            from ..llm.secrets import default_store

            self._secret_store = default_store()
        return self._secret_store

    @property
    def llm_settings(self) -> Any:
        return getattr(self.settings, "llm", None)

    # ------------------------------------------------------------------ Seiten

    def companies(self, query: str, page: int = 1, per_page: int = 50) -> str:
        with self.engine.connect() as conn:
            where: list[Any] = [db.company.c.merged_into_id.is_(None)]
            if query:
                like = f"%{query.lower()}%"
                where.append(
                    or_(
                        func.lower(db.company.c.display_name).like(like),
                        func.lower(db.company.c.website_domain).like(like),
                    )
                )
            total = int(
                conn.execute(select(func.count()).select_from(db.company).where(*where)).scalar()
                or 0
            )
            page = max(1, min(page, max(1, -(-total // per_page))))
            rows = [
                dict(r)
                for r in conn.execute(
                    select(db.company)
                    .where(*where)
                    .order_by(db.company.c.entity_kind == "public_buyer", db.company.c.display_name)
                    .limit(per_page)
                    .offset((page - 1) * per_page)
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
        return render.page("Unternehmen", views.companies_page(rows, query, page, total, per_page))

    def profile(self, company_id: int, years: int, estimates: bool) -> tuple[str, dict[str, Any]]:
        with self.engine.connect() as conn:
            data = build_profile(
                conn, company_id, years=years, include_estimates=estimates, as_of=date.today()
            )
        from ..maintenance import edits_of

        with self.engine.connect() as conn:
            company = repo.company_row(conn, company_id)
        from .. import websites

        edit = views.company_edit_block(company, edits_of(self.engine, company_id), self.csrf)
        state = websites.website_state(self.engine, [company_id]).get(company_id, "keine")
        site = views.website_section(
            company,
            state,
            websites.STATE_LABELS[state],
            websites.candidates_of(self.engine, company_id),
            websites.last_search(self.engine, company_id),
            self.csrf,
        )
        html = render.page(
            data["overview"]["display_name"],
            render.profile_page(
                data,
                csrf=self.csrf,
                extra=(
                    ("websites", "Website-Ermittlung", site),
                    ("edit", "Stammdaten pflegen", edit),
                ),
            ),
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
            # same-origin statt no-referrer: Bei no-referrer senden Chromium-Browser bei
            # Formularen „Origin: null“ (Fetch-Standard), wodurch die Herkunftsprüfung jede
            # eigene Speicher-Aktion ablehnte. Externe Quellenlinks erhalten weiterhin keinen
            # Referrer.
            self.send_header("Referrer-Policy", REFERRER_POLICY)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _html(self, html: str, status: int = 200) -> None:
            if app.shell is not None:  # Desktop-Hülle: „Anwendung beenden“ in der Navigation
                html = html.replace(
                    render.APP_ACTIONS,
                    '<form method="post" action="/app/quit" class="inline quit">'
                    f'<input type="hidden" name="csrf" value="{render.e(app.csrf)}">'
                    "<button>Anwendung beenden</button></form>",
                    1,
                )
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
                if query.get("q"):  # alte Suchadresse
                    self._redirect("/companies?q=" + quote(query["q"][0][:100]))
                    return
                self._html(render.page("Übersicht", dashboard_view(app)))
                return
            if path == "/companies":
                self._html(
                    app.companies(
                        query.get("q", [""])[0][:100],
                        _int(query.get("page", ["1"])[0], 1, 1, 10_000),
                    )
                )
                return
            if path == "/customers":
                self._html(render.page("Bestandskunden", customers_view(app)))
                return
            if path == "/products":
                from ..products import matrix_status

                self._html(
                    render.page(
                        "Produkt-Fit",
                        views.products_page(app.catalog, matrix_status(app.catalog), app.cases),
                    )
                )
                return
            if path == "/sources":
                self._html(render.page("Quellen", sources_view(app)))
                return
            if path == "/settings/ai":
                self._html(
                    render.page(
                        "KI-Einstellungen",
                        ai_view(
                            app, query.get("ok", [""])[0][:300], query.get("err", [""])[0][:300]
                        ),
                    )
                )
                return
            if path == "/ai/status.json":
                from ..llm import manager as ai

                chip = views.ai_chip(ai.status(app.engine, app.secret_store, app.llm_settings))
                self._send(
                    200,
                    json.dumps(chip, ensure_ascii=False, default=str).encode("utf-8"),
                    "application/json; charset=utf-8",
                    {"Cache-Control": "no-store"},
                )
                return
            if path == "/settings":
                self._html(
                    render.page(
                        "Einstellungen",
                        settings_view(
                            app, query.get("ok", [""])[0][:300], query.get("err", [""])[0][:300]
                        ),
                    )
                )
                return
            if path == "/favicon.ico":
                body = (
                    resources.files("market_intel.ui").joinpath("static", "icon.png").read_bytes()
                )
                self._send(200, body, "image/png")
                return
            if path == "/health":
                from .. import __version__

                health = {
                    "ok": True,
                    "version": __version__,
                    "worker": app.worker is not None and app.worker.alive(),
                }
                self._send(200, json.dumps(health).encode(), "application/json")
                return
            if path in ("/leads", "/leads.csv"):
                segments = tuple(s for s in query.get("segment", []) if s)
                fresh = freshness(app.engine, app.cases, app.catalog, app.model)
                rows = lead_rows(
                    app.engine,
                    include_no_fit=query.get("nofit", [""])[0] == "1",
                    segments=segments,
                    fresh=fresh,
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
                            render.leads_page(
                                rows,
                                app.model.segments,
                                segments,
                                csrf=app.csrf,
                                stale=len(stale_ids(fresh, include_missing=False)),
                                total=len(fresh),
                                missing=sum(1 for f in fresh.values() if f.status == "fehlt"),
                            ),
                        )
                    )
                return
            if path == "/jobs":
                items = jobs.list_jobs(app.engine)
                prog = {j["id"]: jobs.progress(app.engine, j) for j in items}
                self._html(
                    render.page(
                        "Rechercheaufträge",
                        views.jobs_page(items, prog, app.worker is not None and app.worker.alive()),
                    )
                )
                return
            if path == "/imports":
                self._html(render.page("Importe", imports_view(app)))
                return
            if path == "/websites":
                self._html(
                    render.page(
                        "Website-Prüfung",
                        websites_view(
                            app,
                            _int(query.get("job", ["0"])[0], 0, 0, 10**9),
                            query.get("ok", [""])[0][:300],
                            query.get("err", [""])[0][:300],
                        ),
                    )
                )
                return
            if path == "/maintenance":
                from ..maintenance import duplicate_groups, missing_websites

                q = query.get("q", [""])[0][:100]
                self._html(
                    render.page(
                        "Datenpflege",
                        views.maintenance_page(
                            missing_websites(app.engine, q),
                            duplicate_groups(app.engine),
                            app.csrf,
                            q,
                            query.get("ok", [""])[0][:300],
                        ),
                    )
                )
                return
            if path == "/jobs/new":
                self._html(
                    render.page(
                        "Neuer Rechercheauftrag",
                        views.new_job_page(app.cases, app.csrf, _ai_line(app)),
                    )
                )
                return
            match = re.fullmatch(r"/jobs/(\d+)(\.json)?", path)
            if match:
                job = jobs.get_job(app.engine, int(match.group(1)))
                if job is None:
                    self._error(404, "Auftrag nicht gefunden")
                    return
                prog = jobs.progress(app.engine, job)
                if match.group(2):
                    payload = json.dumps(prog, ensure_ascii=False, default=str)
                    self._send(200, payload.encode("utf-8"), "application/json; charset=utf-8")
                    return
                report_path = None
                if job["research_run_id"]:
                    from ..research import run_parameters

                    out = run_parameters(app.engine, job["research_run_id"]).get("out_dir")
                    candidate = Path(out) / "bericht.md" if out else None
                    report_path = str(candidate) if candidate and candidate.exists() else None
                self._html(
                    render.page(
                        job["title"] or "Auftrag",
                        views.job_page(
                            job,
                            prog,
                            jobs.events_of(app.engine, job["id"]),
                            job_results(app.engine, job.get("research_run_id")),
                            app.csrf,
                            report_path,
                        ),
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
                    self._html(_flash(html, query))
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
            if urlsplit(self.path).path == "/app/focus":  # Zweitstart der Desktop-Hülle
                token = self.headers.get("X-MI-Token", "")
                if app.shell is None or not secrets.compare_digest(token, app.shell.token):
                    self._error(HTTPStatus.FORBIDDEN, "nicht erlaubt")
                    return
                app.shell.focus()
                self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
                return
            if not origin_allowed(
                self.headers.get("Origin"),
                self.headers.get("Referer"),
                self.headers.get("Sec-Fetch-Site"),
                app.allowed_hosts,
            ):
                self._error(HTTPStatus.FORBIDDEN, "Fremde Herkunft")
                return
            ctype = self.headers.get("Content-Type", "")
            multipart = ctype.startswith("multipart/form-data")
            limit = MAX_UPLOAD if multipart else MAX_BODY
            length = _int(self.headers.get("Content-Length", "0"), 0, 0, limit + 1)
            if length > limit:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Anfrage zu groß")
                return
            raw = self.rfile.read(length)
            files: dict[str, tuple[str, bytes]] = {}
            if multipart:
                form, files = parse_multipart(raw, ctype)
            else:
                form = parse_qs(raw.decode("utf-8", errors="replace"))
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
            match = re.fullmatch(r"/company/(\d+)/outcome", path)
            if match:
                company_id = int(match.group(1))
                try:
                    record_outcome(
                        app.engine,
                        company_id,
                        form.get("outcome", [""])[0],
                        occurred_on=date.fromisoformat(form.get("occurred_on", [""])[0]),
                        author=form.get("author", [""])[0],
                        value=form.get("value", [""])[0] or None,
                        currency=form.get("currency", [""])[0] or None,
                        reason=form.get("reason", [""])[0],
                    )
                except ValueError as exc:  # OutcomeError und ungültiges Datum
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._redirect(f"/company/{company_id}/opportunity#outcome")
                return
            match = re.fullmatch(r"/websites/(\d+)/(confirm|reject)", path)
            if match or path == "/websites/confirm-clear":
                from .. import websites

                back = _safe_back(form.get("back", ["/websites"])[0])
                author = form.get("author", [""])[0]
                try:
                    if path.endswith("confirm-clear"):
                        ids = [int(i) for i in form.get("ids", [""])[0].split(",") if i.isdigit()]
                        confirmed = websites.confirm_clear(app.engine, ids, author)
                        msg = f"{len(confirmed)} Websites bestätigt"
                    elif match and match.group(2) == "confirm":
                        confirmed = [
                            websites.confirm(
                                app.engine, int(match.group(1)), author, form.get("reason", [""])[0]
                            )
                        ]
                        msg = "Website bestätigt"
                    else:
                        assert match is not None
                        websites.reject(
                            app.engine, int(match.group(1)), author, form.get("reason", [""])[0]
                        )
                        confirmed, msg = [], "Vorschlag abgelehnt"
                except websites.WebsiteError as exc:
                    self._redirect(_with_param(back, "err", str(exc)))
                    return
                if confirmed:  # bestätigte Website auswerten und neu bewerten (Hintergrund)
                    jobs.create_company_job(app.engine, "analyze", confirmed)
                    msg += " – Analyse und Neubewertung als Auftrag eingereiht"
                self._redirect(_with_param(back, "ok", msg))
                return
            match = re.fullmatch(r"/company/(\d+)/websites/search", path)
            if match:
                job_id = jobs.create_company_job(app.engine, "websites", [int(match.group(1))])
                self._redirect(f"/jobs/{job_id}")
                return
            match = re.fullmatch(r"/settings/ai/([a-z-]+)", path)
            if match:
                ok, msg = ai_action(app, match.group(1), form)
                anchor = form.get("provider", [""])[0]
                target = _with_param("/settings/ai", "ok" if ok else "err", msg)
                self._redirect(target + (f"#ki-{anchor}" if anchor.isalnum() else ""))
                return
            if path == "/settings/backup":
                from ..backup import BackupError, create_backup, sqlite_path

                try:
                    info = create_backup(app.engine, sqlite_path(app.engine).parent / "backups")
                    done = f"Sicherung geprüft ({info.companies} Firmen): {info.path.name}"
                    target = "/settings?ok=" + quote(done)
                except BackupError as exc:
                    target = "/settings?err=" + quote(str(exc))
                self._redirect(target)
                return
            if path == "/app/quit":
                if app.shell is None:
                    self._error(404, "nicht gefunden")
                    return
                self._html(
                    render.page(
                        "Beenden",
                        "<h1>Market Intelligence wird beendet</h1><p>Laufende Aufträge werden "
                        "angehalten und beim nächsten Start fortgesetzt. Dieses Fenster kann "
                        "geschlossen werden.</p>",
                    )
                )
                threading.Timer(0.3, app.shell.quit).start()
                return
            if path == "/imports":
                try:
                    imported = import_from_form(app, form, files)
                except ValueError as exc:
                    self._html(
                        render.page("Importe", imports_view(app, error=str(exc))),
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._html(render.page("Importe", imports_view(app, result=imported)))
                return
            if path == "/imports/research":
                list_name = form.get("list_name", [""])[0].strip()
                try:
                    job_id = research_list(app, list_name)
                except (jobs.JobError, ValueError) as exc:
                    self._html(
                        render.page("Importe", imports_view(app, error=str(exc))),
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._redirect(f"/jobs/{job_id}")
                return
            match = re.fullmatch(r"/company/(\d+)/edit", path)
            if match:
                from ..maintenance import MaintenanceError, set_field, set_website

                company_id = int(match.group(1))
                field = form.get("field", [""])[0]
                value = form.get("value", [""])[0]
                author = form.get("author", [""])[0]
                reason = form.get("reason", [""])[0]
                back = _safe_back(form.get("back", [f"/company/{company_id}#edit"])[0])
                try:
                    if field == "website_domain":
                        set_website(app.engine, company_id, value, author, reason)
                    else:
                        set_field(app.engine, company_id, field, value, author, reason)
                except MaintenanceError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._redirect(back)
                return
            if path in ("/maintenance/merge", "/maintenance/distinct"):
                from ..maintenance import MaintenanceError, mark_distinct, merge

                ids = [int(i) for i in form.get("ids", [""])[0].split(",") if i.isdigit()]
                author = form.get("author", [""])[0]
                try:
                    if path.endswith("merge"):
                        keep = int(form.get("keep", ["0"])[0] or 0)
                        if keep not in ids:
                            raise MaintenanceError("Bitte die zu behaltende Firma wählen.")
                        for other in ids:
                            if other != keep:
                                merge(app.engine, keep, other, author, form.get("reason", [""])[0])
                        done = (
                            f"{len(ids) - 1} Datensatz/Datensätze in Firma {keep} zusammengeführt"
                        )
                    else:
                        for i, a in enumerate(ids):
                            for b in ids[i + 1 :]:
                                mark_distinct(app.engine, a, b, author)
                        done = "Als verschiedene Firmen markiert"
                except MaintenanceError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._redirect("/maintenance?ok=" + quote(done))
                return
            if path == "/jobs":
                try:
                    job_id = create_job_from_form(app, form)
                except (jobs.JobError, ValueError) as exc:
                    self._html(
                        render.page(
                            "Neuer Rechercheauftrag",
                            views.new_job_page(app.cases, app.csrf, _ai_line(app), str(exc), form),
                        ),
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._redirect(f"/jobs/{job_id}")
                return
            match = re.fullmatch(r"/jobs/(\d+)/(pause|cancel|resume)", path)
            if match:
                job_id = int(match.group(1))
                action: Any = {
                    "pause": jobs.request_pause,
                    "cancel": jobs.request_cancel,
                    "resume": jobs.resume_job,
                }[match.group(2)]
                try:
                    action(app.engine, job_id)
                except jobs.JobError as exc:
                    self._error(HTTPStatus.CONFLICT, str(exc))
                    return
                self._redirect(f"/jobs/{job_id}")
                return
            if path == "/assessments/refresh" and app.worker is not None:
                job_id = jobs.create_reassess_job(app.engine)
                self._redirect(f"/jobs/{job_id}")
                return
            if path == "/assessments/refresh":
                result = refresh(
                    app.engine, app.cases, app.catalog, app.model, trigger="lead-liste"
                )
                back = form.get("back", ["/leads"])[0]
                self._redirect(
                    (back if back.startswith("/") and not back.startswith("//") else "/leads")
                    + f"?refreshed={result['reassessed']}"
                )
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


def parse_multipart(
    body: bytes, content_type: str
) -> tuple[dict[str, list[str]], dict[str, tuple[str, bytes]]]:
    """multipart/form-data (Datei-Upload) mit der Standardbibliothek auswerten."""
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    fields: dict[str, list[str]] = {}
    files: dict[str, tuple[str, bytes]] = {}
    if not message.is_multipart():
        return fields, files
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        payload = part.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, data)
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", errors="replace"))
    return fields, files


def _flash(html: str, query: dict[str, list[str]]) -> str:
    """Rückmeldung einer Aktion (``?ok=`` / ``?err=``) oben im Inhalt anzeigen."""
    ok = query.get("ok", [""])[0][:300]
    err = query.get("err", [""])[0][:300]
    box = (f'<p class="okbox" role="status">{render.e(ok)}</p>' if ok else "") + (
        f'<div class="errorbox" role="alert">{render.e(err)}</div>' if err else ""
    )
    return html.replace("<main>", "<main>" + box, 1) if box else html


def _with_param(url: str, key: str, value: str) -> str:
    base, _, frag = url.partition("#")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{key}={quote(value)}" + (f"#{frag}" if frag else "")


def websites_view(app: App, job_id: int = 0, message: str = "", error: str = "") -> str:
    from .. import websites

    job = jobs.get_job(app.engine, job_id) if job_id else None
    ids = None
    if job and job.get("research_run_id"):
        with app.engine.connect() as conn:
            ids = [
                int(i)
                for i in conn.execute(
                    select(func.distinct(db.run_candidate.c.company_id)).where(
                        db.run_candidate.c.run_id == job["research_run_id"],
                        db.run_candidate.c.selected.is_(True),
                    )
                ).scalars()
            ]
    counts = websites.summary(websites.website_state(app.engine, ids)) if ids is not None else {}
    return views.websites_review_page(
        websites.pending(app.engine, ids), counts, app.csrf, job=job, message=message, error=error
    )


def _safe_back(back: str) -> str:
    """Nur eigene relative Pfade als Rücksprung (kein offener Redirect)."""
    return back if back.startswith("/") and not back.startswith("//") else "/"


def dashboard_view(app: App) -> str:
    from ..backup import BackupError, list_backups, sqlite_path
    from ..maintenance import duplicate_groups, missing_websites

    fresh = freshness(app.engine, app.cases, app.catalog, app.model)
    leads = lead_rows(app.engine, fresh=fresh)
    with app.engine.connect() as conn:
        companies = conn.execute(
            select(func.count())
            .select_from(db.company)
            .where(
                db.company.c.merged_into_id.is_(None), db.company.c.entity_kind != "public_buyer"
            )
        ).scalar()
        running = conn.execute(
            select(func.count())
            .select_from(db.job)
            .where(db.job.c.status.in_(["queued", "running"]))
        ).scalar()
        since = db.utcnow() - __import__("datetime").timedelta(days=30)
        ok = conn.execute(
            select(func.count())
            .select_from(db.fetch_log)
            .where(
                db.fetch_log.c.at >= since,
                db.fetch_log.c.status.in_(["ok", "cached", "not_modified"]),
            )
        ).scalar()
    try:
        backups = list_backups(sqlite_path(app.engine).parent / "backups")
    except BackupError:
        backups = []
    stats = {
        "companies": companies or 0,
        "leads": sum(1 for r in leads if r["aktualitaet"] == "aktuell"),
        "stale": len(stale_ids(fresh, include_missing=False)),
        "unassessed": sum(1 for f in fresh.values() if f.status == "fehlt"),
        "running": running or 0,
        "customers": len(customer_rows(app)),
        "sources_ok": ok or 0,
        "missing_websites": len(missing_websites(app.engine, limit=100_000)),
        "duplicates": len(duplicate_groups(app.engine)),
        "backup": bool(backups),
        "contact_email": bool(getattr(app.settings, "contact_email", "")) if app.settings else True,
        "website_pending": __import__("market_intel.websites", fromlist=["x"]).count_pending(
            app.engine
        ),
    }
    current = [r for r in leads if r["aktualitaet"] == "aktuell"][:5]
    return views.dashboard_page(stats, jobs.list_jobs(app.engine, 5), current)


def customer_rows(app: App) -> list[dict[str, Any]]:
    from ..opportunity.outcomes import current_outcome

    lists = _customer_lists(app)
    out: dict[int, dict[str, Any]] = {}
    with app.engine.connect() as conn:
        for cid, name, web, list_name in conn.execute(
            select(
                db.company.c.id, db.company.c.display_name, db.company.c.website_domain,
                db.list_membership.c.list_name,
            )
            .join(db.list_membership, db.list_membership.c.company_id == db.company.c.id)
            .where(db.list_membership.c.list_name.in_(lists))
        ):  # fmt: skip
            out.setdefault(
                cid, {"id": cid, "name": name, "website": web, "basis": f"Liste {list_name}"}
            )
        won = (
            conn.execute(
                select(db.sales_outcome.c.company_id).where(
                    db.sales_outcome.c.outcome == "gewonnen"
                )
            )
            .scalars()
            .all()
        )
    for cid in set(won):
        outcome = current_outcome(app.engine, cid)
        if outcome and outcome["outcome"] == "gewonnen":
            with app.engine.connect() as conn:
                row = repo.company_row(conn, cid)
            out.setdefault(
                cid,
                {"id": cid, "name": row["display_name"], "website": row["website_domain"],
                 "basis": f"gewonnen am {outcome['occurred_on']}"},
            )  # fmt: skip
    return sorted(out.values(), key=lambda r: str(r["name"]).lower())


def customers_view(app: App) -> str:
    return views.customers_page(customer_rows(app), _customer_lists(app))


def sources_view(app: App) -> str:
    from datetime import timedelta

    from ..sources.registry import SOURCES

    since = db.utcnow() - timedelta(days=30)
    stats: dict[str, dict[str, int]] = {}
    with app.engine.connect() as conn:
        for key, status, n in conn.execute(
            select(db.fetch_log.c.source_key, db.fetch_log.c.status, func.count())
            .where(db.fetch_log.c.at >= since)
            .group_by(db.fetch_log.c.source_key, db.fetch_log.c.status)
        ):
            bucket = "ok" if status in ("ok", "cached", "not_modified") else "failed"
            stats.setdefault(key, {}).setdefault(bucket, 0)
            stats[key][bucket] += int(n)
    return views.sources_page(list(SOURCES), stats)


def settings_view(app: App, message: str = "", error: str = "") -> str:
    from .. import __version__
    from ..backup import BackupError, list_backups, sqlite_path

    try:
        database = sqlite_path(app.engine)
        backups = list_backups(database.parent / "backups")
        db_text = str(database)
        logs = str(database.parent / "logs" / "market_intel.log")
    except BackupError:
        backups, db_text, logs = [], str(app.engine.url.render_as_string(hide_password=True)), "–"
    settings = app.settings
    info = {
        "Version": __version__,
        "Datenbank": db_text,
        "Protokolldatei": logs,
        "Ausgabeordner": str(app.out_dir),
        "Hintergrund-Worker": "läuft" if app.worker is not None and app.worker.alive() else "aus",
        "Kontaktadresse (User-Agent)": (getattr(settings, "contact_email", "") or "nicht gesetzt")
        if settings
        else "unbekannt",
        "KI": _ai_line(app),
        "Bewertungsmodell": app.model.version,
    }
    return views.settings_page(info, backups, app.csrf, message, error)


def _list_overview(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            {"list_name": n, "members": int(c), "imported_at": t}
            for n, c, t in conn.execute(
                select(
                    db.list_membership.c.list_name,
                    func.count(func.distinct(db.list_membership.c.company_id)),
                    func.max(db.list_membership.c.imported_at),
                )
                .group_by(db.list_membership.c.list_name)
                .order_by(db.list_membership.c.list_name)
            )
        ]


def _customer_lists(app: App) -> list[str]:
    return [str(x) for x in app.model.section("crm").get("customer_lists", [])]


def imports_view(app: App, result: Any = None, error: str = "") -> str:
    shown = None
    if result is not None:
        shown = {
            "list_name": result[0],
            "rows": [
                {
                    "line": r.line,
                    "name": r.name,
                    "status": r.status,
                    "detail": r.detail,
                    "company_id": r.company_id,
                }
                for r in result[1].rows
            ],
            "counts": {
                k: result[1].count(k) for k in ("neu", "zugeordnet", "doppelt", "übersprungen")
            },
        }
    return views.imports_page(
        _list_overview(app.engine), _customer_lists(app), app.cases, app.csrf, shown, error
    )


def import_from_form(
    app: App, form: dict[str, list[str]], files: dict[str, tuple[str, bytes]]
) -> tuple[str, Any]:
    from ..sources.user_data import import_company_bytes

    upload = files.get("file")
    if upload is None or not upload[1].strip():
        raise ValueError("Bitte eine CSV-Datei auswählen.")
    filename, content = upload
    if form.get("kind", ["list"])[0] == "customers":
        lists = _customer_lists(app)
        list_name = form.get("customer_list", [lists[0] if lists else ""])[0]
        if list_name not in lists:
            raise ValueError("Keine Bestandskundenliste konfiguriert (opportunity.toml [crm]).")
    else:
        list_name = form.get("list_name", [""])[0].strip()[:120]
        if list_name in _customer_lists(app):
            raise ValueError(
                "Dieser Name ist für Bestandskunden reserviert – Art „Bestandskunden“."
            )
    result = import_company_bytes(
        app.engine,
        content,
        filename=filename[:200] or "upload.csv",
        list_name=list_name,
        source_url=form.get("source_url", [""])[0].strip()[:500],
        publisher=form.get("publisher", [""])[0].strip()[:200] or None,
    )
    return list_name, result


def research_list(app: App, list_name: str) -> int:
    """Rechercheauftrag für alle Firmen einer importierten Liste."""
    members = {r["list_name"]: r["members"] for r in _list_overview(app.engine)}
    if list_name not in members:
        raise ValueError(f"Liste „{list_name}“ existiert nicht.")
    if list_name in _customer_lists(app):
        raise ValueError("Bestandskunden werden nicht als neue Leads recherchiert.")
    count = min(int(members[list_name]), 200)
    request = ResearchRequest(task=f"Firmenliste „{list_name}“ prüfen", lists=[list_name],
                              max_candidates=count)  # fmt: skip
    stamp = db.utcnow().strftime("%Y%m%d-%H%M%S")
    return jobs.create_research_job(
        app.engine,
        request,
        Budget(max_candidates=count, max_requests=max(300, count * 25)),
        app.out_dir / f"liste-{stamp}",
        title=f"Liste prüfen: {list_name}",
    )


def _list(form: dict[str, list[str]], name: str, sep: str = ",") -> list[str]:
    values: list[str] = []
    for raw in form.get(name, []):
        values.extend(v.strip() for v in raw.replace("\r", "").split(sep) if v.strip())
    return list(dict.fromkeys(values))


def _bounded(form: dict[str, list[str]], name: str, default: int, low: int, high: int) -> int:
    raw = form.get(name, [""])[0].strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"„{name}“ muss eine ganze Zahl sein") from exc
    if not low <= value <= high:
        raise ValueError(f"„{name}“ muss zwischen {low} und {high} liegen")
    return value


def create_job_from_form(app: App, form: dict[str, list[str]]) -> int:
    """Formular → Rechercheauftrag (geprüft; ungültige Eingaben werden gemeldet)."""
    use_cases = [u for u in form.get("use_case", []) if u]
    unknown = [u for u in use_cases if u not in app.cases]
    if unknown:
        raise ValueError(f"Unbekannte Anwendungsfälle: {', '.join(unknown)}")
    countries = [c.upper() for c in form.get("country", []) if re.fullmatch(r"[A-Za-z]{2}", c)]
    request = ResearchRequest(
        task=form.get("task", [""])[0].strip()[:2000],
        countries=countries,
        industries=_list(form, "industry"),
        companies=_list(form, "companies", "\n"),
        use_cases=use_cases,
        keywords=_list(form, "keywords"),
        max_candidates=_bounded(form, "max_candidates", 10, 1, 200),
    )
    budget = Budget(
        max_requests=_bounded(form, "max_requests", 300, 10, 5000),
        max_candidates=request.max_candidates,
        max_seconds=_bounded(form, "max_minutes", 30, 1, 600) * 60,
    )
    stamp = db.utcnow().strftime("%Y%m%d-%H%M%S")
    return jobs.create_research_job(
        app.engine,
        request,
        budget,
        app.out_dir / f"auftrag-{stamp}",
        title=form.get("title", [""])[0],
        no_llm=form.get("use_llm", [""])[0] != "1",
    )


def job_results(engine: Engine, run_id: int | None) -> list[dict[str, Any]]:
    """Ausgewählte Unternehmen eines Auftrags mit ihrer gespeicherten Bewertung."""
    if not run_id:
        return []
    from ..opportunity.assess import latest_assessments

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                db.run_candidate.c.company_id,
                db.run_candidate.c.reason,
                db.company.c.display_name,
                db.company.c.website_domain,
                db.company.c.country_code,
            )
            .join(db.company, db.company.c.id == db.run_candidate.c.company_id)
            .where(db.run_candidate.c.run_id == run_id, db.run_candidate.c.selected.is_(True))
            .order_by(db.run_candidate.c.id)
        ).all()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r.company_id in out:
            continue
        latest = latest_assessments(engine, r.company_id)
        best = latest[0] if latest else None
        details = json.loads(best["details"]) if best else {}
        out[r.company_id] = {
            "id": r.company_id,
            "name": r.display_name,
            "website": r.website_domain,
            "country": r.country_code,
            "reason": r.reason,
            "fit": best["product_relevance"] if best else None,
            "fit_status": (details.get("assessment") or {}).get("fit_status"),
            "segment": (details.get("segment") or {}).get("name"),
        }
    return sorted(out.values(), key=lambda x: -(x["fit"] or -1))


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
    port: int = 8780,  # nicht 8765: dort lauscht NOVA
    discovery: Discovery | None = None,
    **options: Any,
) -> ThreadingHTTPServer:
    """Server erzeugen (Port 0 = frei wählen, für Tests).

    ``discovery`` = (Anwendungsfälle, Produktmatrix, Bewertungsmodell); Standard: mitgeliefert.
    ``options``: ``worker`` (Hintergrund-Worker), ``out_dir``, ``llm_available``.
    """
    server = ThreadingHTTPServer(
        (host, port), make_handler(App(engine, host, port, discovery, **options))
    )
    if port == 0:
        actual = server.server_address[1]
        server.RequestHandlerClass = make_handler(
            App(engine, host, int(actual), discovery, **options)
        )
    server.daemon_threads = True
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


# ---------------------------------------------------------------------- KI-Einstellungen (Phase H)


def _ai_line(app: App) -> str:
    """Kurzbeschreibung des aktiven Providers (Auftragsformular, Einstellungen)."""
    from ..llm import manager as ai

    try:
        selection = ai.resolve(app.engine, app.secret_store, app.llm_settings)
    except Exception as exc:
        return f"KI-Status nicht ermittelbar ({exc})"
    label = ai.PROVIDER_INFO[selection.provider]["label"]
    entry = ai.last_probe(selection.provider)
    state = "" if entry is None else (" · verbunden" if entry[1].ok else f" · {entry[1].message}")
    return f"{label}{state}"


def ai_view(app: App, message: str = "", error: str = "") -> str:
    from ..llm import manager as ai
    from ..llm.nova_setup import find_cli

    state = ai.status(app.engine, app.secret_store, app.llm_settings)
    values = ai.get_settings(app.engine)
    state["models"] = {n: ai.model_for(app.engine, n, values) for n in ai.PROVIDER_INFO}
    state["probes"] = {}
    state["model_choices"] = {}
    for name in ai.PROVIDER_INFO:
        entry = ai.last_probe(name)
        if entry:
            state["probes"][name] = entry[1]
            state["model_choices"][name] = list(entry[1].models)
    cli = find_cli()
    llm = app.llm_settings
    return views.ai_settings_page(
        state,
        ai.PROVIDER_INFO,
        ai.FALLBACK_RULES,
        ai.TASK_LABELS,
        ai.recent_calls(app.engine, 40),
        app.csrf,
        nova_cli=str(cli) if cli else None,
        local_enabled=bool(llm is not None and llm.enabled),
        message=message,
        error=error,
    )


def ai_action(app: App, action: str, form: dict[str, list[str]]) -> tuple[bool, str]:
    """POST-Aktionen der KI-Einstellungen; Meldungen ohne Schlüsselwerte."""
    from ..llm import manager as ai
    from ..llm.nova_setup import NovaSetupError, create_access, find_cli, start_service
    from ..llm.secrets import SecretStoreError

    provider = form.get("provider", [""])[0]
    store = app.secret_store
    if action in ("use", "key", "key-remove", "test", "model") and provider not in ai.PROVIDER_INFO:
        return False, "Unbekannter Provider"
    label = ai.PROVIDER_INFO.get(provider, {}).get("label", provider)
    try:
        if action == "use":
            ai.set_setting(app.engine, "provider", provider, "oberflaeche")
            ai.forget_probe()
            result = ai.probe(app.engine, provider, store, app.llm_settings)
            selection = ai.resolve(app.engine, store, app.llm_settings)
            note = "" if result.ok else f" – Verbindungstest: {result.message}"
            return (
                result.ok,
                f"Aktiv: {ai.PROVIDER_INFO[selection.provider]['label']} "
                f"({selection.reason}){note}",
            )
        if action == "key":
            if provider == "lokal":
                return False, "Der lokale Server braucht keinen Schlüssel"
            value = form.get("key", [""])[0].strip()
            store.set(provider, value)
            ai.forget_probe(provider)
            result = ai.probe(app.engine, provider, store, app.llm_settings)
            return result.ok, (
                f"{label}: Schlüssel gespeichert ({store.label}). Verbindungstest: {result.message}"
            )
        if action == "key-remove":
            removed = store.delete(provider)
            ai.forget_probe(provider)
            if not removed:
                return False, f"{label}: kein Schlüssel hinterlegt"
            selection = ai.resolve(app.engine, store, app.llm_settings)
            return True, (
                f"{label}: Schlüssel gelöscht. Aktiv: "
                f"{ai.PROVIDER_INFO[selection.provider]['label']} ({selection.reason})"
            )
        if action == "test":
            result = ai.probe(app.engine, provider, store, app.llm_settings)
            return result.ok, f"{label}: {result.message}"
        if action == "model":
            model = form.get("model", [""])[0].strip()
            if not model or len(model) > 120 or not re.fullmatch(r"[A-Za-z0-9._:/-]+", model):
                return False, "Ungültiger Modellname"
            ai.set_setting(app.engine, f"model.{provider}", model, "oberflaeche")
            ai.forget_probe(provider)
            result = ai.probe(app.engine, provider, store, app.llm_settings)
            return (
                result.ok,
                f"{label}: Modell {model} gespeichert. Verbindungstest: {result.message}",
            )
        if action == "fallback":
            rule = form.get("rule", [""])[0]
            if rule not in ai.FALLBACK_RULES:
                return False, "Unbekannte Fallback-Regel"
            ai.set_setting(app.engine, "fallback", rule, "oberflaeche")
            return True, "Fallback-Regel gespeichert"
        if action == "nova-setup":
            cli = find_cli()
            if cli is None:
                return False, "NOVA ist auf diesem Rechner nicht installiert"
            start_service(cli)
            prefix = create_access(cli, store)
            ai.forget_probe("nova")
            result = ai.probe(app.engine, "nova", store, app.llm_settings)
            return (
                result.ok,
                f"NOVA-Zugang eingerichtet ({prefix}…). Verbindungstest: {result.message}",
            )
    except (SecretStoreError, NovaSetupError, ValueError) as exc:
        return False, str(exc)
    return False, "Unbekannte Aktion"
