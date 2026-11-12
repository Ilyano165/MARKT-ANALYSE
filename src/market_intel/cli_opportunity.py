"""Kommandos für Customer Discovery (Phase 4): Produkte, Bewertung, Watchlist, Leads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import repo
from .cli_intel import _company_args, resolve_company
from .opportunity.current import freshness, refresh
from .opportunity.leads import export_leads, lead_rows
from .opportunity.model import ModelError, load_model
from .opportunity.report import opportunity_report, to_markdown
from .opportunity.run import all_company_ids, assess_and_store
from .opportunity.watch import STATUS_LABELS, STATUSES, current_entries, set_status
from .products import (
    import_csv,
    load_products,
    matrix_status,
    read_csv_file,
    register_products,
    to_toml,
    validate,
    write_csv_file,
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
        status = matrix_status(catalog)
        print(f"Produktmatrix {status['version']}: {status['label']}")
        if not status["verified"] and status["note"]:
            print(f"  Prüfvermerk: {status['note']}")
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
        write_csv_file(Path(args.file or "produkte.csv"), catalog)
        print(
            f"CSV-Vorlage: {args.file or 'produkte.csv'} (mehrere Werte je Zelle: ein Wert pro "
            "Zeile, in Excel Alt+Enter)"
        )
        return 0
    if args.action == "import-csv":
        if not args.file or not args.out:
            print("Aufruf: mi products import-csv tabelle.csv --out products.toml")
            return 2
        updated, notes = import_csv(read_csv_file(Path(args.file)), catalog)
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
    print(f"  Deckel nach Belegstatus: {model.section('product_relevance')['evidence_caps']}")
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
    print("Hinweis: Priorität = interessant für weitere Prüfung, keine Verkaufsprognose.")
    print(f"Produktmatrix: {matrix_status(catalog)['label']}\n")
    for r in rows[: args.limit]:
        s = r["scores"]
        potential = s["business_potential"] if s["business_potential"] is not None else "?"
        print(
            f"{s['priority']:>3}  {r['company'][:40]:40}  Fit {s['product_relevance']:>3}  "
            f"Pot. {potential!s:>3}  DQ {s['data_quality']:>3}  {r['segment']['name']}"
        )
        a = r["assessment"]
        print(f"     Belegstatus: {a['fit_status']} · Rolle: {a['roles']['label']}")
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
    _, engine, cases, catalog, model = _context(args)
    filters = {
        "context": (cases, catalog, model),
        "include_no_fit": args.include_no_fit,
        "include_excluded": args.include_excluded,
        "min_priority": args.min_priority,
        "segments": tuple(args.segment or ()),
    }
    if args.out:
        files = export_leads(engine, Path(args.out), **filters)
        print("\n".join(str(f) for f in files))
        return 0
    for row in lead_rows(engine, **filters):
        stale = "" if row["aktualitaet"] == "aktuell" else f"  [{row['aktualitaet']}]"
        print(
            f"{row['prioritaet']:>3}  {row['firma'][:40]:40} {row['segment']} · "
            f"{row['bewertungsstatus']}{stale}"
        )
    return 0


def cmd_websites(args: argparse.Namespace) -> int:
    from . import websites
    from .cli import make_ctx
    from .engine.budget import Budget

    settings, engine, _, _, _ = _context(args)
    try:
        if args.action == "pending":
            for c in websites.pending(engine):
                print(
                    f"{c['id']:>5}  {c['company_name'][:36]:36} {c['domain']:32} "
                    f"{c['score']:>3}  {c['verdict_label']}"
                )
            return 0
        if args.action in ("confirm", "reject"):
            if not args.candidate:
                print("Bitte --candidate ID angeben (siehe mi websites pending)")
                return 2
            fn = websites.confirm if args.action == "confirm" else websites.reject
            cid = fn(engine, args.candidate, args.author or "", args.reason or "")
            print(f"Vorschlag {args.candidate}: {args.action} (Firma {cid})")
            return 0
        company_id = resolve_company(engine, args.company, args.company_id)
        if args.action == "discover":
            ctx = make_ctx(settings, engine, Budget(max_requests=80), no_llm=True)
            try:
                out = websites.discover(ctx, company_id)
            finally:
                ctx.gateway.close()
            for source, info in out.get("sources", {}).items():
                print(f"  Quelle {source:10} {info.get('status')}  {info.get('error', '') or ''}")
        state = websites.website_state(engine, [company_id]).get(company_id, "keine")
        print(f"Website-Status: {websites.STATE_LABELS[state]}")
        for c in websites.candidates_of(engine, company_id):
            print(
                f"{c['id']:>5}  {c['domain']:32} {c['score']:>3}  {c['verdict_label']:30} "
                f"[{c['status_label']}]"
            )
            for item in c["details"].get("pro", []):
                print(f"         + {item}")
            for item in c["details"].get("contra", []):
                print(f"         - {item}")
            for item in c["details"].get("unchecked", []):
                print(f"         ? {item}")
    except websites.WebsiteError as exc:
        print(f"Fehler: {exc}")
        return 2
    return 0


def cmd_reassess(args: argparse.Namespace) -> int:
    _, engine, cases, catalog, model = _context(args)
    ids = (
        [resolve_company(engine, args.company, args.company_id)]
        if args.company or args.company_id
        else None
    )
    fresh = freshness(engine, cases, catalog, model, ids)
    counts: dict[str, int] = {}
    for f in fresh.values():
        counts[f.label] = counts.get(f.label, 0) + 1
    print(f"Bewertungsstand (Modell {model.version}):")
    for label, n in sorted(counts.items()):
        print(f"  {n:>5}  {label}")
    if args.check:
        return 0
    result = refresh(
        engine, cases, catalog, model, ids, only_stale=not args.all, trigger="kommandozeile"
    )
    print(
        f"Neu bewertet: {result['reassessed']}; übersprungen (neuere Runde vorhanden): "
        f"{result['skipped_newer_exists']}"
    )
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    from datetime import date

    from .opportunity.outcomes import (
        OUTCOMES,
        OutcomeError,
        outcome_history,
        outcome_text,
        record_outcome,
    )

    _, engine, _, _, _ = _context(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    if args.status:
        try:
            record_outcome(
                engine,
                company_id,
                args.status,
                occurred_on=date.fromisoformat(args.date) if args.date else date.today(),
                author=args.author or "",
                value=args.value,
                currency=args.currency,
                use_case_key=args.use_case,
                reason=args.reason or "",
            )
        except (OutcomeError, ValueError) as exc:
            print(f"Nicht gespeichert: {exc}")
            return 2
        print(f"Gespeichert: {OUTCOMES[args.status]}")
    for entry in outcome_history(engine, company_id):
        print(f"{outcome_text(entry)} · {entry['author']} · {entry['reason']}")
    return 0


def cmd_outcomes_report(args: argparse.Namespace) -> int:
    from .opportunity.outcomes import evaluate, evaluation_markdown

    _, engine, _, _, _ = _context(args)
    text = evaluation_markdown(evaluate(engine, args.min_cases))
    if args.md:
        Path(args.md).write_text(text, "utf-8")
        print(f"Auswertung: {args.md}")
    else:
        print(text)
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

    out = sub.add_parser(
        "outcome", help="Vertriebsergebnis eintragen (Kontakt, Angebot, gewonnen, verloren)"
    )
    _company_args(out)
    out.add_argument("--status", choices=["kontakt", "angebot", "gewonnen", "verloren"])
    out.add_argument("--date", help="Datum JJJJ-MM-TT (Standard: heute)")
    out.add_argument("--value", help="Auftrags-/Angebotswert, nur wenn bekannt")
    out.add_argument("--currency", help="Währung zum Wert, z. B. EUR")
    out.add_argument("--use-case", help="Anwendungsfall-Schlüssel, falls bekannt")
    out.add_argument("--reason", help="Grund, z. B. warum verloren")
    out.add_argument("--author")
    out.set_defaults(func=cmd_outcome)

    rep = sub.add_parser(
        "outcomes-report", help="Auswertung Bewertung ↔ Ergebnis (nur erfasste Daten)"
    )
    rep.add_argument("--md", help="Markdown-Datei schreiben")
    rep.add_argument("--min-cases", type=int, default=10, help="Anteile erst ab n Fällen")
    rep.set_defaults(func=cmd_outcomes_report)

    ws = sub.add_parser("websites", help="Website-Ermittlung: suchen, prüfen, bestätigen")
    ws.add_argument("action", choices=["discover", "list", "pending", "confirm", "reject"])
    _company_args(ws)
    ws.add_argument("--candidate", type=int, help="Vorschlags-Nr. (bei confirm/reject)")
    ws.add_argument("--author", help="wer entscheidet (Pflicht bei confirm/reject)")
    ws.add_argument("--reason", help="Begründung")
    ws.set_defaults(func=cmd_websites)

    ra = sub.add_parser(
        "reassess", help="Bewertungsstand prüfen und veraltete Bewertungen neu berechnen"
    )
    _company_args(ra)
    ra.add_argument("--check", action="store_true", help="nur anzeigen, nichts neu bewerten")
    ra.add_argument("--all", action="store_true", help="auch aktuelle Bewertungen neu berechnen")
    ra.set_defaults(func=cmd_reassess)

    lead = sub.add_parser("leads", help="Lead-Liste anzeigen oder exportieren (CSV/XLSX/JSON)")
    lead.add_argument("--out", help="Ordner für leads.csv/.xlsx/.json")
    lead.add_argument("--min-priority", type=int, default=0)
    lead.add_argument("--segment", nargs="*")
    lead.add_argument("--include-no-fit", action="store_true")
    lead.add_argument(
        "--include-excluded", action="store_true", help="auch Bestandskunden und verworfene"
    )
    lead.set_defaults(func=cmd_leads)
