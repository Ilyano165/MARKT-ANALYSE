"""Kommandos für Customer Discovery (Phase 4): Produkte, Bewertung, Watchlist, Leads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import repo
from .cli_intel import _company_args, resolve_company
from .opportunity.leads import export_leads, lead_rows
from .opportunity.model import ModelError, load_model
from .opportunity.report import opportunity_report, to_markdown
from .opportunity.run import all_company_ids, assess_and_store
from .opportunity.watch import STATUS_LABELS, STATUSES, current_entries, set_status
from .products import (
    export_csv,
    import_csv,
    load_products,
    register_products,
    to_toml,
    validate,
)


def _context(args: argparse.Namespace) -> tuple[Any, Any, Any, Any, Any]:
    from .cli import _setup

    settings, engine, cases = _setup(args)
    repo.sync_sources(engine, (), (), False)
    catalog = load_products(settings.products_file)
    model = load_model(settings.opportunity_file)
    register_products(engine, catalog)
    return settings, engine, cases, catalog, model


# ---------------------------------------------------------------------- Produktmatrix


def cmd_products(args: argparse.Namespace) -> int:
    from .cli import _setup
    from .usecases import load_use_cases

    settings, _, cases = _setup(args)
    if args.action == "validate":
        catalog = load_products(Path(args.file) if args.file else settings.products_file)
        if args.use_cases_file:
            cases = load_use_cases(Path(args.use_cases_file))
        problems = validate(catalog, cases)
        for problem in problems:
            print(f"- {problem}")
        print(
            f"{len(catalog.products)} Produktgruppen, {len(cases)} Anwendungsfälle: "
            + ("in Ordnung" if not problems else f"{len(problems)} Probleme")
        )
        return 1 if problems else 0
    catalog = load_products(settings.products_file)
    if args.action == "list":
        for group in catalog.products.values():
            print(f"{group.key:24} {group.status:11} {group.name}")
            print(f"{'':24} Anwendungsfälle: {', '.join(group.applications)}")
        return 0
    if args.action == "show":
        shown = catalog.products.get(args.file or "")
        if shown is None:
            print(f"Unbekannte Produktgruppe; verfügbar: {', '.join(catalog.products)}")
            return 2
        print(json.dumps(shown.as_dict(), ensure_ascii=False, indent=1))
        return 0
    if args.action == "export-csv":
        Path(args.file or "produkte.csv").write_text(export_csv(catalog), "utf-8-sig")
        print(f"CSV-Vorlage: {args.file or 'produkte.csv'} (Listen mit ';' getrennt)")
        return 0
    if args.action == "import-csv":
        if not args.file or not args.out:
            print("Aufruf: mi products import-csv tabelle.csv --out products.toml")
            return 2
        updated, notes = import_csv(Path(args.file).read_text("utf-8-sig"), catalog)
        for note in notes:
            print(f"- {note}")
        problems = validate(updated, cases)
        Path(args.out).write_text(to_toml(updated), "utf-8")
        print(f"geschrieben: {args.out}; Prüfung: {problems or 'in Ordnung'}")
        print(f'Aktivieren mit products_file = "{args.out}" in market_intel.toml')
        return 1 if problems else 0
    return 2


def cmd_model(args: argparse.Namespace) -> int:
    from .cli import _setup

    settings, _, _ = _setup(args)
    try:
        model = load_model(Path(args.file) if args.file else settings.opportunity_file)
    except ModelError as exc:
        print(f"Modell ungültig: {exc}")
        return 1
    print(f"Modell {model.version}: in Ordnung")
    print(f"  Priorität: {model.section('model')['priority_weights']}")
    print(f"  Produktrelevanz: {model.section('product_relevance')['weights']}")
    print(f"  Potenzial: {model.section('business_potential')['weights']}")
    print("  Segmente (in Reihenfolge):")
    for segment in model.segments:
        conditions = " UND ".join(f"{f} {op} {v}" for f, op, v in segment.conditions) or "sonst"
        print(f"    {segment.key}: {conditions}")
    return 0


# ---------------------------------------------------------------------- Bewertung


def cmd_opportunities(args: argparse.Namespace) -> int:
    _, engine, cases, catalog, model = _context(args)
    if args.company or args.company_id:
        ids = [resolve_company(engine, args.company, args.company_id)]
    else:
        ids = all_company_ids(engine)
    best = assess_and_store(engine, ids, cases, catalog, model)
    rows = sorted(best.values(), key=lambda r: -r["scores"]["priority"])
    print(f"{len(rows)} Unternehmen bewertet (Modell {model.version}; jede Bewertung gespeichert)")
    print("Hinweis: Priorität = interessant für weitere Prüfung, keine Verkaufsprognose.\n")
    for r in rows[: args.limit]:
        s = r["scores"]
        potential = s["business_potential"] if s["business_potential"] is not None else "?"
        print(
            f"{s['priority']:>3}  {r['company'][:40]:40}  Fit {s['product_relevance']:>3}  "
            f"Pot. {potential!s:>3}  DQ {s['data_quality']:>3}  {r['segment']['name']}"
        )
        if r["scores"]["product_relevance"]:
            print(f"     {r['use_case_name']}")
    return 0


def cmd_opportunity(args: argparse.Namespace) -> int:
    _, engine, cases, catalog, model = _context(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    report = opportunity_report(engine, company_id, cases, catalog, model)
    markdown = to_markdown(report)
    if args.md:
        Path(args.md).write_text(markdown, "utf-8")
        print(f"Bericht: {args.md}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, default=str, indent=1), "utf-8"
        )
        print(f"JSON: {args.json}")
    if not args.md and not args.json:
        print(markdown)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    _, engine, _, _, _ = _context(args)
    if args.company or args.company_id:
        company_id = resolve_company(engine, args.company, args.company_id)
        set_status(
            engine,
            company_id,
            args.status,
            watchlist=args.list,
            reason=args.reason or "",
            author=args.author or "",
        )
        if args.comment:
            with engine.begin() as conn:
                repo.add_note(conn, company_id, "opportunity", args.comment, args.author or "")
        print(f"Status gesetzt: {STATUS_LABELS[args.status]} ({args.list})")
    for entry in current_entries(engine, None if args.all_lists else args.list):
        print(
            f"{entry['watchlist']:12} {entry['display_name'][:40]:40} "
            f"{STATUS_LABELS[entry['status']]:24} {entry['created_at']:%Y-%m-%d} {entry['reason']}"
        )
    return 0


def cmd_leads(args: argparse.Namespace) -> int:
    _, engine, _, _, _ = _context(args)
    filters = {
        "include_no_fit": args.include_no_fit,
        "min_priority": args.min_priority,
        "segments": tuple(args.segment or ()),
    }
    if args.out:
        files = export_leads(engine, Path(args.out), **filters)
        print("\n".join(str(f) for f in files))
        return 0
    for row in lead_rows(engine, **filters):
        print(
            f"{row['prioritaet']:>3}  {row['firma'][:40]:40} {row['segment']} · "
            f"{row['bewertungsstatus']}"
        )
    return 0


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Phase-4-Kommandos registrieren."""
    p = sub.add_parser("products", help="Yellotools-Produktmatrix anzeigen, prüfen, pflegen")
    p.add_argument("action", choices=["list", "show", "validate", "export-csv", "import-csv"])
    p.add_argument("file", nargs="?", help="Datei bzw. Produktschlüssel (bei show)")
    p.add_argument("--out", help="Zieldatei für import-csv (TOML)")
    p.add_argument("--use-cases-file", help="eigene Anwendungsmatrix für validate")
    p.set_defaults(func=cmd_products)

    m = sub.add_parser("opportunity-model", help="Bewertungsmodell und Segmente anzeigen/prüfen")
    m.add_argument("file", nargs="?")
    m.set_defaults(func=cmd_model)

    o = sub.add_parser("opportunities", help="Unternehmen bewerten (alle oder eines)")
    _company_args(o)
    o.add_argument("--limit", type=int, default=50)
    o.set_defaults(func=cmd_opportunities)

    r = sub.add_parser("opportunity", help="Ergebnis je Unternehmen (elf Abschnitte)")
    _company_args(r)
    r.add_argument("--md", help="Markdown-Datei schreiben")
    r.add_argument("--json", help="JSON-Datei schreiben")
    r.set_defaults(func=cmd_opportunity)

    w = sub.add_parser("watch", help="Watchlist: vormerken, Status setzen, anzeigen")
    _company_args(w)
    w.add_argument("--status", default="vorgemerkt", choices=list(STATUSES))
    w.add_argument("--list", default="Leads", help="Name der Watchlist")
    w.add_argument("--reason")
    w.add_argument("--author")
    w.add_argument("--comment", help="interne Notiz zum Unternehmen")
    w.add_argument("--all-lists", action="store_true")
    w.set_defaults(func=cmd_watch)

    lead = sub.add_parser("leads", help="Lead-Liste anzeigen oder exportieren (CSV/XLSX/JSON)")
    lead.add_argument("--out", help="Ordner für leads.csv/.xlsx/.json")
    lead.add_argument("--min-priority", type=int, default=0)
    lead.add_argument("--segment", nargs="*")
    lead.add_argument("--include-no-fit", action="store_true")
    lead.set_defaults(func=cmd_leads)
