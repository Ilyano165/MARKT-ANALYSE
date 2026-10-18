"""Kommandozeile ``mi``. Läuft ohne bezahlte Zugangsdaten und ohne KI."""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
from pathlib import Path

from sqlalchemy import Engine, select

from . import __version__, db
from .config import Settings, load_settings
from .context import Ctx
from .http.gateway import FetchGateway
from .llm.local import NullLLM, build_llm
from .proof import evaluate, write_report
from .repo import sync_sources
from .research import create_run, execute, standard_tasks
from .sources.registry import SOURCES
from .usecases import UseCase, load_use_cases, register

DEFAULT_COMPARE = ["3M", "Avery Dennison", "ORAFOL"]
DEFAULT_USE_CASES = ["fahrzeugfolierung", "glasfolien"]


def _setup(args: argparse.Namespace) -> tuple[Settings, Engine]:
    settings = load_settings(Path(args.config) if args.config else Path("market_intel.toml"))
    if args.db:
        settings = dataclasses.replace(settings, database_url=args.db)
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    return settings, engine


def _ctx(settings: Settings, engine: Engine, *, no_llm: bool) -> Ctx:
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
        max_requests=settings.max_requests_per_run,
    )
    llm = NullLLM() if no_llm else build_llm(settings.llm)
    if not settings.contact_email:
        print(
            "Hinweis: contact_email in market_intel.toml fehlt. SEC und Wikimedia verlangen "
            "einen User-Agent mit Kontakt; Abrufe können abgelehnt werden.",
            file=sys.stderr,
        )
    return Ctx(engine=engine, gateway=gateway, settings=settings, llm=llm)


def cmd_sources(args: argparse.Namespace) -> int:
    settings, engine = _setup(args)
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


def cmd_run(args: argparse.Namespace) -> int:
    settings, engine = _setup(args)
    cases = load_use_cases(Path(args.use_cases) if args.use_cases else None)
    register(engine, cases)
    unknown = [u for u in args.use_case if u not in cases]
    if unknown:
        print(f"Unbekannte Anwendungsfälle: {unknown}. Verfügbar: {sorted(cases)}")
        return 2
    out_dir = Path(args.out)
    tasks = standard_tasks(
        args.compare, args.use_case, args.country, str(out_dir / "export"), args.max_websites
    )
    run_id = create_run(
        engine,
        "proof" if args.proof else "standard",
        {"compare": args.compare, "use_cases": args.use_case, "country": args.country},
        tasks,
    )
    print(
        f"Run {run_id} angelegt ({len(tasks)} Aufgaben). Strg+C pausiert; fortsetzen mit "
        f"`mi resume {run_id}`."
    )
    return _execute(settings, engine, run_id, cases, args.no_llm or args.proof, out_dir, args.proof)


def cmd_resume(args: argparse.Namespace) -> int:
    settings, engine = _setup(args)
    cases = load_use_cases(Path(args.use_cases) if args.use_cases else None)
    with engine.connect() as conn:
        run = (
            conn.execute(select(db.research_run).where(db.research_run.c.id == args.run_id))
            .mappings()
            .first()
        )
    if run is None:
        print(f"Run {args.run_id} existiert nicht")
        return 2
    return _execute(
        settings,
        engine,
        args.run_id,
        cases,
        args.no_llm,
        Path(args.out),
        run["run_type"] == "proof",
    )


def _execute(
    settings: Settings,
    engine: Engine,
    run_id: int,
    cases: dict[str, UseCase],
    no_llm: bool,
    out_dir: Path,
    proof: bool,
) -> int:
    ctx = _ctx(settings, engine, no_llm=no_llm)
    try:
        status = execute(ctx, run_id, cases)
    finally:
        ctx.gateway.close()
    print(f"Run {run_id}: {status}")
    if status == "paused":
        return 130
    with engine.connect() as conn:
        summary = json.loads(
            conn.execute(
                select(db.research_run.c.summary).where(db.research_run.c.id == run_id)
            ).scalar()
            or "{}"
        )
    for note in summary.get("notes", [])[:25]:
        print(f"  - {note}")
    if proof:
        criteria = evaluate(engine, run_id, out_dir, ctx.llm.name)
        environment = (
            f"{platform.system()} {platform.release()}, Python "
            f"{platform.python_version()}, market-intel {__version__}"
        )
        report = write_report(out_dir, run_id, criteria, summary, environment)
        for c in criteria:
            print(f"  [{'OK ' if c.passed else 'NEIN'}] {c.number}. {c.title}")
        print(f"Bericht: {report}")
        return 0 if all(c.passed for c in criteria) else 1
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    settings, _ = _setup(args)
    llm = build_llm(settings.llm)
    print(f"Lokale KI eingeschaltet: {settings.llm.enabled}; aktiv: {llm.name}")
    if settings.llm.enabled and llm.name == "none":
        print(
            f"Ollama unter {settings.llm.base_url} mit Modell {settings.llm.model} nicht "
            "erreichbar – die Anwendung arbeitet regelbasiert weiter."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi", description="Yellotools Market Intelligence")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="Einstellungsdatei (Standard: market_intel.toml)")
    parser.add_argument("--db", help="Datenbank-URL, z. B. sqlite:///mi.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="Quellen mit Lizenz, Kosten und Status anzeigen").set_defaults(
        func=cmd_sources
    )

    for name, proof in (("run", False), ("prove", True)):
        p = sub.add_parser(
            name,
            help="Recherchelauf" if not proof else "Funktionsnachweis (realer Lauf, Kriterien 1–9)",
        )
        p.add_argument("--compare", nargs="+", default=DEFAULT_COMPARE)
        p.add_argument("--use-case", nargs="+", default=DEFAULT_USE_CASES)
        p.add_argument("--country", default="DE")
        p.add_argument("--max-websites", type=int, default=10)
        p.add_argument("--use-cases", help="eigene Anwendungsfall-Datei (TOML)")
        p.add_argument("--out", default="out" if not proof else "proof")
        p.add_argument("--no-llm", action="store_true", help="lokale KI nicht verwenden")
        p.set_defaults(func=cmd_run, proof=proof)

    r = sub.add_parser("resume", help="pausierten Run fortsetzen")
    r.add_argument("run_id", type=int)
    r.add_argument("--out", default="out")
    r.add_argument("--use-cases")
    r.add_argument("--no-llm", action="store_true")
    r.set_defaults(func=cmd_resume)

    sub.add_parser("llm-status", help="lokale KI prüfen").set_defaults(func=cmd_llm)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
