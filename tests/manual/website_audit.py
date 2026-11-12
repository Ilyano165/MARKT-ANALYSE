"""Echter Zuordnungstest der Website-Ermittlung mit bekannten richtigen und falschen Websites.

    python tests/manual/website_audit.py --home /tmp/mi-audit --out website_audit.json

Importiert Firmen **ohne** Website, lässt die Ermittlung gegen das echte Internet laufen und
vergleicht mit den bekannten Soll-Werten. Die Soll-Domains sind das öffentlich bekannte
Firmen-Hauptangebot (Allgemeinwissen, nicht aus der Anwendung abgeleitet); ``verboten`` sind
bekannte Fehlzuordnungen. Nichts wird bestätigt – das ist ein Test der Vorschläge.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import select

from market_intel import db, websites
from market_intel.cli import make_ctx
from market_intel.config import Settings
from market_intel.engine.budget import Budget
from market_intel.sources.user_data import import_company_list
from market_intel.usecases import load_use_cases, register

# name, land, ort, erwartet (None = es darf keinen eindeutigen Treffer geben), verboten, Art
CASES: list[tuple[str, str, str, set[str] | None, set[str], str]] = [
    (
        "ORAFOL Europe GmbH",
        "DE",
        "Oranienburg",
        {"orafol.com", "orafol.de"},
        set(),
        "Hersteller, groß",
    ),
    (
        "Hornbach Baumarkt AG",
        "DE",
        "Bornheim",
        {"hornbach.de", "hornbach.com"},
        set(),
        "Handel, groß",
    ),
    (
        "Beiersdorf AG",
        "DE",
        "Hamburg",
        {"beiersdorf.de", "beiersdorf.com"},
        set(),
        "Konsumgüter, groß",
    ),
    (
        "Adolf Würth GmbH & Co. KG",
        "DE",
        "Künzelsau",
        {"wuerth.de", "wuerth.com", "würth.de"},
        set(),
        "Handel/Befestigung, groß",
    ),
    ("CFC CarFilmComponents e. K.", "DE", "", {"cfc.de"}, set(), "Werbetechnik, klein"),
    (
        "Check Matt Fahrzeugfolierung GmbH",
        "DE",
        "Dortmund",
        {"check-matt.de"},
        set(),
        "Werbetechnik, klein",
    ),
    (
        "DEKO.TEAM Inh. Laura Bonner",
        "DE",
        "",
        None,
        {"deko-team.de"},
        "Falle: ähnlicher Name, fremde Firma",
    ),
    ("Muster Werbetechnik GmbH", "DE", "Musterstadt", None, set(), "Falle: Allerweltsname"),
    ("Müller Verwaltungs GmbH", "DE", "Berlin", None, set(), "Falle: Allerweltsname, keine Quelle"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite:///{home.as_posix()}/audit.db", output_dir=home / "out"
    )
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)
    register(engine, load_use_cases())
    ctx = make_ctx(settings, engine, Budget(max_requests=600), no_llm=True)
    csv = home / "firmen.csv"
    csv.write_text(
        "name;country;city\n" + "\n".join(f"{n};{c};{o}" for n, c, o, *_ in CASES) + "\n",
        encoding="utf-8",
    )
    import_company_list(
        engine, csv, list_name="Phase-I-Audit", source_url="lokal://audit", publisher=None
    )
    with engine.connect() as conn:
        ids = {
            r.display_name: r.id
            for r in conn.execute(select(db.company.c.id, db.company.c.display_name))
        }
    rows: list[dict[str, Any]] = []
    for name, _country, _city, expect, forbid, kind in CASES:
        cid = ids[name]
        started = time.monotonic()
        outcome = websites.discover(ctx, cid)
        seconds = round(time.monotonic() - started, 1)
        cands = websites.candidates_of(engine, cid)
        proposed = [c for c in cands if c["status"] == "vorgeschlagen"]
        domains = [c["domain"] for c in proposed]
        wrong = [c for c in proposed if c["domain"] in forbid]
        if expect is None:
            ok = not wrong and all(c["verdict"] != "eindeutig" for c in proposed)
            verdict = "kein eindeutiger Vorschlag" if ok else "FALSCHER/zu sicherer Vorschlag"
        else:
            hit = [c for c in proposed if c["domain"] in expect]
            ok = bool(hit) and not wrong and hit[0]["domain"] == proposed[0]["domain"]
            verdict = (
                f"richtig vorgeschlagen ({hit[0]['verdict']}, {hit[0]['score']})"
                if hit
                else "RICHTIGE WEBSITE NICHT VORGESCHLAGEN"
            )
        print(
            f"{'OK  ' if ok else 'FAIL'} {name:36} {seconds:6.1f}s {verdict} | Vorschläge={domains}",
            flush=True,
        )
        for c in proposed:
            pro = [str(e)[:70] for e in c["details"].get("pro", [])][:3]
            con = [str(e)[:70] for e in c["details"].get("contra", [])][:3]
            print(
                f"       - {c['domain']:24} {c['score']:>4} {c['verdict']:16} pro={pro} contra={con}",
                flush=True,
            )
        rows.append(
            {
                "firma": name,
                "art": kind,
                "ok": ok,
                "bewertung": verdict,
                "sekunden": seconds,
                "vorschlaege": [
                    {"domain": c["domain"], "score": c["score"], "urteil": c["verdict"]}
                    for c in cands
                ],
                "suche": str(outcome)[:300],
            }
        )
    with engine.connect() as conn:
        fetch = conn.execute(select(db.fetch_log.c.status, db.fetch_log.c.source_key)).all()
    by_status: dict[str, int] = {}
    for status, _source in fetch:
        by_status[str(status)] = by_status.get(str(status), 0) + 1
    payload = {
        "faelle": rows,
        "abrufe": by_status,
        "bestanden": sum(r["ok"] for r in rows),
        "gesamt": len(rows),
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {"bestanden": payload["bestanden"], "gesamt": payload["gesamt"], "abrufe": by_status},
            ensure_ascii=False,
        )
    )
    return 0 if payload["bestanden"] == payload["gesamt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
