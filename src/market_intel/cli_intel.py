"""Kommandos der Unternehmensanalyse (Phase 3): Profil, Oberfläche, Wettbewerb, Erfassung."""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Engine, select

from . import db, repo
from .domain import METRIC_LABELS
from .engine.changes import history
from .intel import manual
from .intel.competitors import CLASS_LABELS, analyze_competitors
from .intel.profile import build_profile
from .resolution import normalize_name
from .sources.user_data import store_document


def _setup(args: argparse.Namespace) -> Engine:
    from .cli import _setup as setup

    _, engine, _ = setup(args)
    repo.sync_sources(engine, (), (), False)
    return engine


def resolve_company(engine: Engine, name: str | None, company_id: int | None) -> int:
    """Firmen-ID aus ID oder eindeutigem Namen/Alias."""
    with engine.connect() as conn:
        if company_id:
            if conn.execute(select(db.company.c.id).where(db.company.c.id == company_id)).first():
                return int(company_id)
            raise SystemExit(f"Firma {company_id} nicht gefunden")
        wanted = normalize_name(name or "")
        ids = sorted(
            set(
                conn.execute(
                    select(db.company_alias.c.company_id).where(
                        db.company_alias.c.normalized == wanted
                    )
                ).scalars()
            )
        )
    if len(ids) != 1:
        raise SystemExit(
            f"Firma „{name}“ {'nicht gefunden' if not ids else 'mehrdeutig'} ({ids}); "
            "bitte --company-id angeben."
        )
    return int(ids[0])


def _document(engine: Engine, args: argparse.Namespace) -> int:
    if args.document_id:
        return int(args.document_id)
    if not args.file:
        raise SystemExit("Beleg fehlt: --document-id oder --file angeben")
    path = Path(args.file)
    content = path.read_bytes()
    ctype = (
        "application/pdf"
        if content[:5] == b"%PDF-"
        else ("text/html" if path.suffix.lower() in (".html", ".htm") else "text/plain")
    )
    return store_document(
        engine,
        "user_upload",
        content,
        url=args.source_url or f"upload:{path.name}",
        title=args.title or path.name,
        publisher=args.publisher,
        published=date.fromisoformat(args.published) if args.published else None,
        content_type=ctype,
    )


def _decimal(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation as exc:
        raise SystemExit(f"Keine Zahl: {value}") from exc


# ---------------------------------------------------------------------- Befehle


def cmd_profile(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    if args.html:
        from .ui.server import export_profile_html

        Path(args.html).write_text(
            export_profile_html(engine, company_id, args.years, args.estimates), "utf-8"
        )
        print(f"HTML-Profil: {args.html}")
    with engine.connect() as conn:
        profile = build_profile(
            conn, company_id, years=args.years, include_estimates=args.estimates
        )
    if args.json:
        Path(args.json).write_text(
            json.dumps(profile, ensure_ascii=False, default=str, indent=1), "utf-8"
        )
        print(f"JSON-Profil: {args.json}")
    if not args.html and not args.json:
        _print_profile(profile)
    return 0


def _fmt(value: object) -> str:
    if value is None:
        return "unbekannt"
    dec = Decimal(str(value))
    return f"{dec:,.0f}".replace(",", ".")


def _print_profile(profile: dict[str, object]) -> None:
    o = profile["overview"]
    assert isinstance(o, dict)
    print(f"{o['display_name']} – offizieller Name: {o['official_name'] or 'unbekannt'}")
    hq = o["headquarters"]
    print(f"  Hauptsitz: {(hq['city'] + ' ' + (hq['country'] or '')) if hq else 'unbekannt'}")
    for key, label in (("latest_revenue", "Umsatz"), ("latest_headcount", "Mitarbeiter")):
        point = o[key]
        if point is None:
            print(f"  {label}: unbekannt")
        else:
            print(
                f"  {label}: {_fmt(point['value'])} "
                f"{'Personen' if point['unit'] == 'persons' else point['unit']} ({point['label']}, "
                f"{point['scope_label']}, {point['provenance_label']}) [{point['source_ref']}]"
            )
    for section in ("finance", "employees"):
        data = profile[section]
        assert isinstance(data, dict)
        for series in data["revenue"] if section == "finance" else data["series"]:
            print(f"\n  {series['title']}")
            for row in series["rows"]:
                point = row["point"]
                change = (
                    f"{float(row['change_pct']):+.1f} %".replace(".", ",")
                    if row["change_pct"] is not None
                    else ""
                )
                print(
                    f"    {row['label']:>8}  {_fmt(point['value']) if point else 'unbekannt':>16}"
                    f"  {change:>9}  {row['status']}"
                )
            index = series["total_index"]
            if index is not None:
                span = series["spans"][index]
                print(f"    Gesamt {span['start_label']}–{span['end_label']}: {span['formula']}")
                for note in span["notes"]:
                    print(f"      Hinweis: {note}")
    if o["unknown"]:
        print(f"\n  Unbekannt: {', '.join(o['unknown'])}")
    print(f"\n  {len(profile['sources'])} Quellen; Details: mi profile … --html profil.html")  # type: ignore[arg-type]


def cmd_ui(args: argparse.Namespace) -> int:
    from .cli import _setup as setup
    from .opportunity.model import load_model
    from .products import load_products
    from .ui.server import make_server

    settings, engine, cases = setup(args)
    repo.sync_sources(engine, (), (), False)
    discovery = (
        cases,
        load_products(settings.products_file),
        load_model(settings.opportunity_file),
    )
    if args.host not in ("127.0.0.1", "localhost") and not args.allow_lan:
        raise SystemExit(
            "Die Oberfläche hat keine Anmeldung. Andere Adressen als 127.0.0.1 nur mit "
            "--allow-lan in vertrauenswürdigen Netzen."
        )
    server = make_server(engine, args.host, args.port, discovery)
    print(f"Oberfläche: http://{args.host}:{server.server_address[1]}/  (Beenden: Strg+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("beendet")
    finally:
        server.server_close()
    return 0


def cmd_competitors(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    others = [resolve_company(engine, n, None) for n in args.with_ or []] or None
    with engine.begin() as conn:
        result = analyze_competitors(conn, company_id, others, store=args.store)
    print(f"Wettbewerbsanalyse {result['name']} ({result['method']})")
    print(f"  {result['note']}")
    for key, label in CLASS_LABELS.items():
        items = result["groups"][key]
        if not items:
            continue
        print(f"\n  {label} ({len(items)})")
        for item in items:
            print(f"    {item['name']}: {'; '.join(item['reasons'])}")
            dims = item["dimensions"]
            print(f"      Produkte: {dims['products']['text']}; Umsatz: {dims['revenue']['text']}")
    if args.store:
        print("\n  Einordnung gespeichert.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    with engine.connect() as conn:
        items = history(conn, company_id)
    if not items:
        print("Keine Änderungen erfasst (nur Ersterfassung).")
    for item in items:
        print(
            f"{item['detected_at'][:10]}  Ereignis {item['event_date'] or 'unbekannt':>10}  "
            f"{item['subject']}/{item['kind']}: {item['summary']}"
        )
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    with engine.begin() as conn:
        note_id = repo.add_note(conn, company_id, args.section, args.text, args.author)
    print(f"Notiz {note_id} gespeichert.")
    return 0


def _run_manual(func: object, **kwargs: object) -> int:
    try:
        result = func(**kwargs)  # type: ignore[operator]
    except manual.ManualEntryError as exc:
        print(f"Abgelehnt: {exc}")
        return 2
    print(f"Gespeichert: {result}")
    return 0


def cmd_add_figure(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    value = _decimal(args.value)
    assert value is not None
    return _run_manual(
        manual.add_figure,
        engine=engine,
        company_id=company_id,
        document_id=_document(engine, args),
        quote=args.quote,
        locator=args.locator or "",
        metric=args.metric,
        value=value,
        period_end=date.fromisoformat(args.period_end),
        period_start=date.fromisoformat(args.period_start) if args.period_start else None,
        currency=args.currency,
        scope=args.scope,
        provenance=args.provenance,
        segment=args.segment or "",
        methodology=args.methodology or "",
        limitations=args.limitations or "",
    )


def cmd_add_event(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    return _run_manual(
        manual.add_event,
        engine=engine,
        company_id=company_id,
        document_id=_document(engine, args),
        quote=args.quote,
        locator=args.locator or "",
        event_type=args.type,
        event_date=args.date,
        summary=args.summary or "",
        provenance=args.provenance,
        counterparty=args.counterparty,
        affected_headcount=args.affected_headcount,
        amount=_decimal(args.amount),
        currency=args.currency,
        country=args.country,
    )


def cmd_add_location(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    return _run_manual(
        manual.add_location,
        engine=engine,
        company_id=company_id,
        document_id=_document(engine, args),
        quote=args.quote,
        locator=args.locator or "",
        location_type=args.type,
        country=args.country,
        city=args.city,
        postal_code=args.postal_code,
        street=args.street,
        latitude=_decimal(args.lat),
        longitude=_decimal(args.lon),
        geo_precision=args.geo_precision,
        geo_note=args.geo_note or "",
        status=args.status,
        provenance=args.provenance,
    )


def cmd_add_fact(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    return _run_manual(
        manual.add_fact,
        engine=engine,
        company_id=company_id,
        document_id=_document(engine, args),
        quote=args.quote,
        locator=args.locator or "",
        fact_type=args.type,
        value=args.value,
        provenance=args.provenance,
    )


def cmd_add_market(args: argparse.Namespace) -> int:
    engine = _setup(args)
    company_id = resolve_company(engine, args.company, args.company_id)
    return _run_manual(
        manual.add_market,
        engine=engine,
        company_id=company_id,
        document_id=_document(engine, args),
        quote=args.quote,
        locator=args.locator or "",
        country=args.country,
        region=args.region or "",
        presence=args.presence,
        provenance=args.provenance,
    )


# ---------------------------------------------------------------------- Parser


def _company_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--company", help="Firmenname wie in der Datenbank")
    p.add_argument("--company-id", type=int)


def _evidence_args(p: argparse.ArgumentParser) -> None:
    _company_args(p)
    p.add_argument("--document-id", type=int, help="bereits gespeichertes Dokument")
    p.add_argument("--file", help="Beleg-Datei (PDF/HTML/TXT), wird gespeichert")
    p.add_argument("--source-url", help="Original-URL des Belegs (nur wenn bekannt)")
    p.add_argument("--published", help="Veröffentlichungsdatum JJJJ-MM-TT (nur wenn bekannt)")
    p.add_argument("--publisher")
    p.add_argument("--title")
    p.add_argument("--quote", required=True, help="wörtliches Zitat aus dem Beleg")
    p.add_argument("--locator", help="Fundstelle, z. B. „S. 12, Lagebericht“")
    p.add_argument(
        "--provenance",
        default="company_reported",
        choices=["official", "company_reported", "third_party", "derived", "estimate"],
    )


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Phase-3-Kommandos am Hauptparser registrieren."""
    p = sub.add_parser("profile", help="Unternehmensprofil (Text, --html, --json)")
    _company_args(p)
    p.add_argument("--years", type=int, default=10, help="Geschäftsjahre (5–10 empfohlen)")
    p.add_argument("--estimates", action="store_true", help="Schätzungen einbeziehen")
    p.add_argument("--html", help="eigenständige HTML-Datei schreiben")
    p.add_argument("--json", help="JSON-Datei schreiben")
    p.set_defaults(func=cmd_profile)

    u = sub.add_parser("ui", help="lokale Weboberfläche starten")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--allow-lan", action="store_true", help="andere Adressen erlauben (ohne Login)")
    u.set_defaults(func=cmd_ui)

    c = sub.add_parser("competitors", help="Wettbewerbsanalyse (ohne Marktanteile)")
    _company_args(c)
    c.add_argument("--with", dest="with_", nargs="*", help="nur mit diesen Firmen vergleichen")
    c.add_argument("--store", action="store_true", help="Einordnung in der Historie speichern")
    c.set_defaults(func=cmd_competitors)

    h = sub.add_parser("history", help="erkannte Veränderungen einer Firma")
    _company_args(h)
    h.set_defaults(func=cmd_history)

    n = sub.add_parser("note", help="interne Notiz hinzufügen")
    _company_args(n)
    n.add_argument("--section", default="notes", choices=list(repo.NOTE_SECTIONS))
    n.add_argument("--author", required=True)
    n.add_argument("text")
    n.set_defaults(func=cmd_note)

    f = sub.add_parser("add-figure", help="Kennzahl mit Beleg erfassen")
    _evidence_args(f)
    f.add_argument("--metric", required=True, choices=list(METRIC_LABELS))
    f.add_argument("--value", required=True)
    f.add_argument("--currency", help="ISO 4217 bei Umsatz")
    f.add_argument("--period-end", required=True, help="Stichtag/Geschäftsjahresende JJJJ-MM-TT")
    f.add_argument("--period-start", help="Geschäftsjahresbeginn (erkennt Rumpfgeschäftsjahre)")
    f.add_argument("--scope", default="unknown", choices=["group", "single_entity", "unknown"])
    f.add_argument("--segment", help="z. B. country:DE oder site:Leipzig")
    f.add_argument("--methodology", help="Pflicht bei Schätzung/Ableitung")
    f.add_argument("--limitations")
    f.set_defaults(func=cmd_add_figure)

    ev = sub.add_parser("add-event", help="Ereignis (Übernahme, Investition …) mit Beleg")
    _evidence_args(ev)
    ev.add_argument("--type", required=True, choices=list(repo.EVENT_TYPES))
    ev.add_argument("--date", help="JJJJ, JJJJ-MM oder JJJJ-MM-TT (Ereignisdatum laut Quelle)")
    ev.add_argument("--summary")
    ev.add_argument("--counterparty")
    ev.add_argument("--affected-headcount", type=int)
    ev.add_argument("--amount")
    ev.add_argument("--currency")
    ev.add_argument("--country")
    ev.set_defaults(func=cmd_add_event)

    lo = sub.add_parser("add-location", help="Standort mit Beleg erfassen")
    _evidence_args(lo)
    lo.add_argument("--type", required=True, choices=list(repo.LOCATION_TYPES))
    lo.add_argument("--country")
    lo.add_argument("--city")
    lo.add_argument("--postal-code")
    lo.add_argument("--street")
    lo.add_argument("--lat")
    lo.add_argument("--lon")
    lo.add_argument("--geo-precision", default="none", choices=list(repo.GEO_PRECISIONS))
    lo.add_argument("--geo-note", help="Herkunft der Koordinaten (Pflicht mit --lat/--lon)")
    lo.add_argument("--status", default="active", choices=["active", "announced", "closed"])
    lo.set_defaults(func=cmd_add_location)

    fa = sub.add_parser("add-fact", help="Produktgruppe, Marke, Vertriebsweg … mit Beleg")
    _evidence_args(fa)
    fa.add_argument("--type", required=True, choices=list(repo.FACT_TYPES))
    fa.add_argument("--value", required=True)
    fa.set_defaults(func=cmd_add_fact)

    ma = sub.add_parser("add-market", help="Absatzland/-region mit Beleg")
    _evidence_args(ma)
    ma.add_argument("--country")
    ma.add_argument("--region")
    ma.add_argument("--presence", required=True, choices=list(repo.MARKET_PRESENCE))
    ma.set_defaults(func=cmd_add_market)
