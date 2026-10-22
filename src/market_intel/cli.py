"""Kommandozeile ``mi``. Läuft ohne bezahlte Zugangsdaten und ohne KI."""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, select

from . import __version__, db, repo
from .config import Settings, load_settings
from .context import Ctx
from .engine.adapters import adapter_overview
from .engine.budget import Budget
from .engine.extract import extract_company_documents
from .engine.report import build_report, step_overview
from .engine.strategy import ResearchRequest
from .http.gateway import FetchGateway
from .llm.assistant import Assistant
from .llm.providers import NullProvider, build_provider
from .proof import evaluate, write_report
from .repo import sync_sources
from .research import create_research, execute, run_parameters, update_budget
from .resolution import normalize_name
from .sources.registry import SOURCES
from .sources.user_data import import_company_list, store_document
from .usecases import UseCase, load_use_cases, register

PROOF_COMPANIES = ["3M", "Avery Dennison", "ORAFOL"]
PROOF_USE_CASES = ["fahrzeugfolierung", "glasfolien"]


def _setup(args: argparse.Namespace) -> tuple[Settings, Engine, dict[str, UseCase]]:
    settings = load_settings(Path(args.config) if args.config else Path("market_intel.toml"))
    if args.db:
        settings = dataclasses.replace(settings, database_url=args.db)
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    explicit = getattr(args, "use_cases", None)
    cases = load_use_cases(Path(explicit) if explicit else settings.use_cases_file)
    register(engine, cases)
    return settings, engine, cases


def make_ctx(settings: Settings, engine: Engine, budget: Budget, *, no_llm: bool) -> Ctx:
    """Kontext mit Gateway (Limits aus Einstellungen und Budget) und optionaler lokaler KI."""
    active = sync_sources(
        engine,
        settings.enabled_sources,
        settings.disabled_sources,
        settings.companies_house_key is not None,
    )
    gateway = FetchGateway(
        engine,
        user_agent=settings.user_agent,
        enabled_sources=active,
        max_requests=min(settings.max_requests_per_run, budget.max_requests),
        timeout=settings.http_timeout_seconds,
        max_attempts=settings.max_attempts,
        min_intervals=dict(settings.rate_limits),
        cost_limit_eur=budget.max_cost_eur,
    )
    provider = (
        NullProvider() if no_llm else build_provider(settings.llm, max_calls=budget.max_llm_calls)
    )
    if not settings.contact_email:
        print(
            "Hinweis: contact_email in market_intel.toml fehlt. SEC und Wikimedia verlangen "
            "einen User-Agent mit Kontakt; Abrufe können abgelehnt werden.",
            file=sys.stderr,
        )
    return Ctx(
        engine=engine, gateway=gateway, settings=settings, ai=Assistant(provider), budget=budget
    )


def _budget(args: argparse.Namespace) -> Budget:
    base = Budget()
    return dataclasses.replace(
        base,
        max_requests=args.max_requests or base.max_requests,
        max_candidates=args.max_candidates or base.max_candidates,
        max_seconds=args.max_seconds or base.max_seconds,
        max_llm_calls=args.max_llm_calls or base.max_llm_calls,
        max_search_hits=args.max_hits or base.max_search_hits,
    )


def _run_and_report(
    settings: Settings,
    engine: Engine,
    cases: dict[str, UseCase],
    run_id: int,
    *,
    no_llm: bool,
    proof_dir: Path | None = None,
) -> int:
    params = run_parameters(engine, run_id)
    budget = Budget.from_dict(params.get("budget", {}))
    ctx = make_ctx(settings, engine, budget, no_llm=no_llm)
    try:
        status = execute(ctx, run_id, cases)
    finally:
        ctx.gateway.close()
    print(f"Auftrag {run_id}: {status}")
    if status in ("paused", "budget_exhausted"):
        print(
            f"Fortsetzen mit: mi resume {run_id}"
            + (" --max-requests … (höheres Budget)" if status == "budget_exhausted" else "")
        )
        return 130 if status == "paused" else 3
    print(f"Bericht: {Path(params['out_dir']) / 'bericht.md'}")
    if proof_dir is not None:
        with engine.connect() as conn:
            summary = json.loads(
                conn.execute(
                    select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
                ).scalar()
                or "{}"
            )
        criteria = evaluate(engine, run_id, proof_dir, ctx.ai.name)
        environment = (
            f"{platform.system()} {platform.release()}, Python "
            f"{platform.python_version()}, market-intel {__version__}"
        )
        path = write_report(proof_dir, run_id, criteria, summary, environment)
        for c in criteria:
            print(f"  [{'OK ' if c.passed else 'NEIN'}] {c.number}. {c.title}")
        print(f"Nachweis: {path}")
        return 0 if all(c.passed for c in criteria) else 1
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    settings, engine, cases = _setup(args)
    if "all" in args.use_case:  # alle Anwendungsfälle der Matrix (branchenübergreifende Suche)
        args.use_case = sorted(cases)
    unknown = [u for u in args.use_case if u not in cases]
    if unknown:
        print(f"Unbekannte Anwendungsfälle {unknown}; verfügbar: {sorted(cases)}")
        return 2
    if not args.task and not (args.company or args.industry or args.use_case):
        print("Bitte eine Aufgabenbeschreibung oder Filter angeben (siehe mi research -h).")
        return 2
    request = ResearchRequest(
        task=args.task or "",
        countries=args.country,
        industries=args.industry,
        companies=args.company,
        use_cases=args.use_case,
        metrics=args.metric,
        company_types=args.company_type,
        lists=args.list,
        years=args.years,
        max_candidates=args.max_candidates or 25,
        refresh=args.refresh,
    )
    run_id = create_research(engine, request, _budget(args), Path(args.out))
    print(f"Auftrag {run_id} angelegt. Strg+C pausiert; fortsetzen mit `mi resume {run_id}`.")
    return _run_and_report(settings, engine, cases, run_id, no_llm=args.no_llm)


def cmd_prove(args: argparse.Namespace) -> int:
    settings, engine, cases = _setup(args)
    request = ResearchRequest(
        task="Vergleiche die Unternehmensentwicklung der genannten Folienhersteller und finde "
        "Unternehmen, die Folien verarbeiten (Fahrzeugfolierung, Glasfolien).",
        countries=[args.country],
        companies=args.company or PROOF_COMPANIES,
        use_cases=PROOF_USE_CASES,
        max_candidates=args.max_candidates or 15,
        years=5,
    )
    budget = dataclasses.replace(Budget(), max_candidates=args.max_candidates or 15)
    out = Path(args.out)
    run_id = create_research(engine, request, budget, out / "auftrag", run_type="proof")
    print(f"Nachweislauf {run_id} angelegt (ohne KI).")
    return _run_and_report(settings, engine, cases, run_id, no_llm=True, proof_dir=out)


def cmd_resume(args: argparse.Namespace) -> int:
    settings, engine, cases = _setup(args)
    with engine.connect() as conn:
        run = (
            conn.execute(select(db.research_run).where(db.research_run.c.id == args.run_id))
            .mappings()
            .first()
        )
    if run is None:
        print(f"Auftrag {args.run_id} existiert nicht")
        return 2
    update_budget(
        engine,
        args.run_id,
        max_requests=args.max_requests,
        max_seconds=args.max_seconds,
        max_llm_calls=args.max_llm_calls,
        max_tasks=args.max_tasks,
    )
    proof_dir = (
        Path(run_parameters(engine, args.run_id)["out_dir"]).parent
        if run["run_type"] == "proof"
        else None
    )
    return _run_and_report(
        settings,
        engine,
        cases,
        args.run_id,
        no_llm=args.no_llm or run["run_type"] == "proof",
        proof_dir=proof_dir,
    )


def cmd_status(args: argparse.Namespace) -> int:
    _, engine, _ = _setup(args)
    with engine.connect() as conn:
        run = (
            conn.execute(select(db.research_run).where(db.research_run.c.id == args.run_id))
            .mappings()
            .first()
        )
        if run is None:
            print(f"Auftrag {args.run_id} existiert nicht")
            return 2
        failed = (
            conn.execute(
                select(db.research_task).where(
                    db.research_task.c.run_id == args.run_id, db.research_task.c.status == "failed"
                )
            )
            .mappings()
            .all()
        )
    print(f"Auftrag {args.run_id} ({run['run_type']}): {run['status']}")
    for row in step_overview(engine, args.run_id):
        print(
            f"  {row['step']}. {row['name']:<28} ok {row.get('succeeded', 0):>3}  "
            f"Fehler {row.get('failed', 0):>3}  übersprungen {row.get('skipped', 0):>3}  "
            f"offen {row.get('pending', 0) + row.get('running', 0):>3}"
        )
    for task in failed[:20]:
        print(f"  FEHLER {task['label']}: {task['error']}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    _, engine, cases = _setup(args)
    out = Path(args.out or run_parameters(engine, args.run_id)["out_dir"])
    for path in build_report(engine, args.run_id, out, Assistant(), cases):
        print(path)
    return 0


def cmd_import_companies(args: argparse.Namespace) -> int:
    _, engine, _ = _setup(args)
    sync_sources(engine, (), (), False)
    result = import_company_list(
        engine,
        Path(args.file),
        list_name=args.list_name,
        source_url=args.source_url or "",
        publisher=args.publisher,
    )
    print(
        f"{len(result.companies)} Firmen aus „{args.list_name}“ übernommen, "
        f"{result.skipped_rows} Zeilen ohne Namen übersprungen (Dokument {result.document_id})."
    )
    print(f'Nutzung in einer Recherche: mi research … --list "{args.list_name}"')
    return 0


def cmd_import_document(args: argparse.Namespace) -> int:
    settings, engine, cases = _setup(args)
    sync_sources(engine, (), (), False)
    with engine.connect() as conn:
        if args.company_id:
            company_id = args.company_id
        else:
            wanted = normalize_name(args.company)
            rows = conn.execute(
                select(db.company_alias.c.company_id).where(db.company_alias.c.normalized == wanted)
            ).all()
            ids = sorted({r.company_id for r in rows})
            if len(ids) != 1:
                print(
                    f"Firma „{args.company}“ {'nicht gefunden' if not ids else 'mehrdeutig'}"
                    f" ({ids}); bitte --company-id angeben."
                )
                return 2
            company_id = ids[0]
    path = Path(args.file)
    content = path.read_bytes()
    ctype = (
        "application/pdf"
        if content[:5] == b"%PDF-"
        else ("text/html" if path.suffix.lower() in (".html", ".htm") else "text/plain")
    )
    published = date.fromisoformat(args.published) if args.published else None
    document_id = store_document(
        engine,
        "user_upload",
        content,
        url=args.source_url or f"upload:{path.name}",
        title=args.title or path.name,
        publisher=args.publisher,
        published=published,
        content_type=ctype,
    )
    ctx = make_ctx(settings, engine, Budget(), no_llm=True)
    report = extract_company_documents(ctx, company_id, [document_id], cases, [])
    ctx.gateway.close()
    with engine.connect() as conn:
        name = repo.company_row(conn, company_id)["display_name"]
    print(
        f"Dokument {document_id} für {name}: {report.observations} Kennzahlen, "
        f"{report.rejected} abgelehnt, {report.signals} Signale; Hinweise: "
        f"{report.issues or 'keine'}"
    )
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    settings, engine, _ = _setup(args)
    active = sync_sources(
        engine,
        settings.enabled_sources,
        settings.disabled_sources,
        settings.companies_house_key is not None,
    )
    for s in SOURCES:
        flag = "AKTIV " if s.key in active else "aus   "
        print(f"{flag} {s.key:16} {s.legal_status:25} Kosten: {s.optional_costs}")
        print(f"       kostenlos: {s.free_scope}; Registrierung: {s.registration}")
    return 0


def cmd_adapters(args: argparse.Namespace) -> int:
    for row in adapter_overview():
        print(
            f"{row['adapter']:16} findet={row['findet_kandidaten']!s:5} "
            f"untersucht={row['untersucht_firmen']!s:5} {row['kinds']}"
        )
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    settings, _, _ = _setup(args)
    provider = build_provider(settings.llm)
    print(
        f"Lokale KI eingeschaltet: {settings.llm.enabled}; Provider: {settings.llm.provider}; "
        f"aktiv: {provider.name}"
    )
    if settings.llm.enabled and provider.name == "none":
        print(
            f"{settings.llm.base_url} mit Modell {settings.llm.model} nicht erreichbar – die "
            "Anwendung arbeitet regelbasiert weiter."
        )
    return 0


def _budget_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-requests", type=int, help="max. Netzabrufe (Standard 1500)")
    p.add_argument("--max-candidates", type=int, help="max. untersuchte Firmen (Standard 25)")
    p.add_argument("--max-seconds", type=int, help="max. Laufzeit in Sekunden")
    p.add_argument("--max-llm-calls", type=int, help="max. Aufrufe der lokalen KI")
    p.add_argument("--max-hits", type=int, help="max. Treffer je Suchanfrage (Standard 20)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi", description="Yellotools Market Intelligence")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="Einstellungsdatei (Standard: market_intel.toml)")
    parser.add_argument("--db", help="Datenbank-URL, z. B. sqlite:///mi.db")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("research", help="Rechercheauftrag (Freitext und/oder Filter)")
    r.add_argument("task", nargs="?", help="Aufgabe in eigenen Worten, Firmennamen in „…“")
    r.add_argument("--country", nargs="*", default=[], help="ISO-Ländercodes, z. B. DE AT")
    r.add_argument("--industry", nargs="*", default=[], help="Branchen-/Suchbegriffe")
    r.add_argument("--company", nargs="*", default=[], help="Firmen für Vergleich/Entwicklung")
    r.add_argument("--use-case", nargs="*", default=[], help="Anwendungsfälle (Schlüssel)")
    r.add_argument("--metric", nargs="*", default=[], help="revenue headcount_total …")
    r.add_argument("--company-type", nargs="*", default=[])
    r.add_argument("--list", nargs="*", default=[], help="importierte Firmenlisten einbeziehen")
    r.add_argument("--years", type=int, help="Zeitraum in Jahren (Standard 5)")
    r.add_argument("--refresh", action="store_true", help="bereits untersuchte Quellen neu abrufen")
    r.add_argument("--use-cases", help="eigene Anwendungsfall-Datei (TOML)")
    r.add_argument("--out", default="out", help="Ausgabeordner")
    r.add_argument("--no-llm", action="store_true")
    _budget_args(r)
    r.set_defaults(func=cmd_research)

    p = sub.add_parser("prove", help="Funktionsnachweis (realer Lauf, Kriterien 1–9, ohne KI)")
    p.add_argument("--company", nargs="*", default=[])
    p.add_argument("--country", default="DE")
    p.add_argument("--max-candidates", type=int)
    p.add_argument("--out", default="proof")
    p.set_defaults(func=cmd_prove)

    s = sub.add_parser("resume", help="pausierten oder budgetbegrenzten Auftrag fortsetzen")
    s.add_argument("run_id", type=int)
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--max-requests", type=int)
    s.add_argument("--max-seconds", type=int)
    s.add_argument("--max-llm-calls", type=int)
    s.add_argument("--max-tasks", type=int)
    s.set_defaults(func=cmd_resume)

    st = sub.add_parser("status", help="Fortschritt je Schritt")
    st.add_argument("run_id", type=int)
    st.set_defaults(func=cmd_status)

    rep = sub.add_parser("report", help="Bericht eines Auftrags neu erzeugen")
    rep.add_argument("run_id", type=int)
    rep.add_argument("--out")
    rep.set_defaults(func=cmd_report)

    ic = sub.add_parser("import-companies", help="Firmenliste (CSV) importieren")
    ic.add_argument("file")
    ic.add_argument("--list-name", required=True)
    ic.add_argument("--source-url", help="Herkunft der Liste (z. B. Ausstellerverzeichnis-URL)")
    ic.add_argument("--publisher")
    ic.set_defaults(func=cmd_import_companies)

    idoc = sub.add_parser("import-document", help="Dokument (z. B. Jahresabschluss) auswerten")
    idoc.add_argument("file")
    idoc.add_argument("--company", help="Firmenname wie in der Datenbank")
    idoc.add_argument("--company-id", type=int)
    idoc.add_argument("--source-url", help="Original-URL (nur wenn bekannt)")
    idoc.add_argument("--published", help="Veröffentlichungsdatum JJJJ-MM-TT (nur wenn bekannt)")
    idoc.add_argument("--publisher")
    idoc.add_argument("--title")
    idoc.set_defaults(func=cmd_import_document)

    sub.add_parser("sources", help="Quellen mit Lizenz, Kosten, Status").set_defaults(
        func=cmd_sources
    )
    sub.add_parser("adapters", help="Adapter und ihre Funktionen").set_defaults(func=cmd_adapters)
    sub.add_parser("llm-status", help="lokale KI prüfen").set_defaults(func=cmd_llm)
    from .cli_intel import add_commands
    from .cli_opportunity import add_commands as add_opportunity_commands

    add_commands(sub)
    add_opportunity_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
