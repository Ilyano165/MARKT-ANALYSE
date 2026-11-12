"""KI-Qualitätsprüfung gegen eine echte NOVA mit bekannten Ausgangsdaten (Phase I).

Aufruf (NOVA läuft, Zugangsschlüssel von ``nova integrations create``):

    MI_NOVA_KEY=nova_… python tests/manual/ai_quality.py --label qwen3.5-9b --out ergebnis.json

Jeder Fall hat **bekannte Eingaben** (synthetisch, ausdrücklich keine echten Firmendaten) und
**überprüfbare Erwartungen**: erwartete Treffer, verbotene Treffer (Erfindungen, Verneinungen,
Fehlzuordnungen), und ob Widersprüche erkannt bzw. nicht erfunden werden. Gemessen werden
Antwortzeit, Anteil verwertbarer Antworten und – über einen mitschneidenden Provider – wie viele
Rohaussagen die deterministische Prüfung der Anwendung verwerfen musste.

Das Skript ist ein Messwerkzeug, kein pytest-Test: Ergebnisse hängen vom Modell ab.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from market_intel.llm.assistant import Assistant, Fact
from market_intel.llm.providers import NovaProvider, ProviderError

LABELS = {
    "fahrzeugfolierung": "Fahrzeugfolierung",
    "glasfolien": "Glas- und Fensterfolien",
    "schilder_werbetechnik": "Schilder, Leuchtreklame, Werbetechnik",
    "zahnmedizin": "Zahnarztpraxis",
    "metallbau": "Metallbau und Schlosserei",
}
USE_CASES = {
    "fahrzeugfolierung": "Fahrzeugfolierung",
    "glasfolien": "Glas- und Fensterfolien",
    "zahnmedizin": "Zahnarztpraxen",
}


class Recorder:
    """Schneidet jede Rohantwort mit, damit sichtbar wird, was die Prüfung verwerfen musste."""

    name = "nova"

    def __init__(self, inner: NovaProvider) -> None:
        self.inner = inner
        self.calls = 0
        self.raw: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.seconds: list[float] = []
        self.model = inner.model

    def available(self) -> bool:
        return True

    def run(self, task: str, user: str, schema: dict[str, Any]) -> Any:
        started = time.monotonic()
        try:
            reply = self.inner.run(task, user, schema)
        except ProviderError as exc:
            self.errors.append(f"{exc.kind}: {exc}")
            self.seconds.append(round(time.monotonic() - started, 1))
            raise
        self.calls += 1
        self.raw.append(reply.data)
        self.seconds.append(round(time.monotonic() - started, 1))
        self.model = reply.model
        return reply

    def complete_json(self, task: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.run(task, user, schema).data  # type: ignore[no-any-return]
        except ProviderError:
            return None


@dataclass
class Case:
    kind: str
    name: str
    run: Callable[[Assistant], Any]
    check: Callable[[Any, list[dict[str, Any]]], tuple[bool, str]]
    result: Any = None
    ok: bool = False
    note: str = ""
    seconds: float = 0.0
    raw: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _has(words: list[str], terms: list[str]) -> bool:
    joined = " ".join(words).lower()
    return any(t in joined for t in terms)


# ---------------------------------------------------------------- Fälle


def cases() -> list[Case]:
    out: list[Case] = []

    # --- Suchstrategie: keine erfundenen Firmennamen, sinnvolle Begriffe
    def strat(task: str, terms: list[str], names: list[str]) -> tuple[Callable, Callable]:
        def run(a: Assistant) -> Any:
            return a.suggest_strategy(task)

        def check(res: Any, raw: list[dict[str, Any]]) -> tuple[bool, str]:
            if not res:
                return False, "keine verwertbare Antwort"
            inv = [
                n for r in raw for n in r.get("company_names", []) if n.lower() not in task.lower()
            ]
            ok_terms = _has(res.get("search_terms", []) + res.get("industry_terms", []), terms)
            ok_names = sorted(res.get("company_names", [])) == sorted(names)
            note = f"Begriffe={res.get('search_terms', [])[:6]} Firmen={res.get('company_names')}"
            if inv:
                note += f" | erfundene Firmennamen verworfen: {inv}"
            return ok_terms and ok_names, note

        return run, check

    for task, terms, names in [
        ("Finde Fahrzeugfolierer in Deutschland.", ["folier", "fahrzeug", "wrap"], []),
        (
            "Analysiere die Entwicklung von ORAFOL über die letzten fünf Jahre.",
            ["orafol", "folie"],
            ["ORAFOL"],
        ),
        (
            "Suche Unternehmen, die Schaufensterbeschriftung in Österreich anbieten.",
            ["schaufenster", "beschrift", "werbetechnik"],
            [],
        ),
    ]:
        run, check = strat(task, terms, names)
        out.append(Case("strategie", task[:50], run, check))

    # --- Tätigkeiten: erwartete, verbotene, Verneinung
    def acts(text: str, expect: set[str], forbid: set[str]) -> tuple[Callable, Callable]:
        def run(a: Assistant) -> Any:
            return a.classify_use_cases(text, LABELS)

        def check(res: Any, raw: list[dict[str, Any]]) -> tuple[bool, str]:
            keys = {q.label for q in res}
            raw_keys = {
                m.get("key") for r in raw for m in (r.get("matches") or []) if isinstance(m, dict)
            }
            note = f"geprüft={sorted(keys)} roh={sorted(str(k) for k in raw_keys)}"
            return expect <= keys and not (keys & forbid), note

        return run, check

    for name, text, expect, forbid in [
        (
            "Folierer",
            "Die Muster Folientechnik GmbH foliert Fahrzeuge und beschriftet Schaufenster in Köln.",
            {"fahrzeugfolierung"},
            {"zahnmedizin", "metallbau"},
        ),
        (
            "Zahnarzt",
            "Unsere Praxis in Leipzig bietet Zahnreinigung, Implantate und Kieferorthopädie für die ganze Familie.",
            {"zahnmedizin"},
            {"fahrzeugfolierung", "glasfolien", "metallbau"},
        ),
        (
            "Verneinung",
            "Wir sind ein Steuerberatungsbüro in Hamburg. Fahrzeugfolierung gehört nicht zu unseren Leistungen.",
            set(),
            {
                "fahrzeugfolierung",
                "glasfolien",
                "schilder_werbetechnik",
                "zahnmedizin",
                "metallbau",
            },
        ),
        (
            "Mischfall",
            "Wir montieren Sonnenschutzfolien an Glasfassaden und fertigen Leuchtreklame für den Einzelhandel.",
            {"glasfolien", "schilder_werbetechnik"},
            {"zahnmedizin", "fahrzeugfolierung"},
        ),
    ]:
        run, check = acts(text, expect, forbid)
        out.append(Case("anwendungsfaelle", name, run, check))

    # --- Passagen: wörtlich und inhaltlich richtig
    ptext = "Die Beispiel Werbetechnik GmbH wurde 1998 gegründet. Der Hauptsitz befindet sich in Köln-Ehrenfeld. Das Unternehmen beschäftigt rund 45 Mitarbeiter und betreibt eine zweite Werkstatt in Düsseldorf."
    out.append(
        Case(
            "passagen",
            "Hauptsitz",
            lambda a: a.relevant_passages(ptext, "Wo hat das Unternehmen seinen Hauptsitz?"),
            lambda res, raw: (
                bool(res)
                and any("Köln" in q.quote for q in res)
                and not any("Düsseldorf" in q.quote and "Köln" not in q.quote for q in res[:1]),
                f"Zitate={[q.quote for q in res]}",
            ),
        )
    )

    # --- Zusammenfassung: nur Fakten, Umsatz nicht erfinden
    def summ(facts: list[Fact], forbid: list[str], expect: list[str]) -> tuple[Callable, Callable]:
        def run(a: Assistant) -> Any:
            return a.summarize(facts)

        def check(res: Any, raw: list[dict[str, Any]]) -> tuple[bool, str]:
            rejected = bool(raw) and not res
            if not res:
                return (
                    False,
                    "verworfen (Prüfung: Referenzen/Zahlen)" if rejected else "keine Antwort",
                )
            low = res.lower()
            bad = [w for w in forbid if w in low]
            miss = [w for w in expect if w not in low]
            return not bad and not miss, f"Text={res[:220]!r} verboten={bad} fehlt={miss}"

        return run, check

    f_base = [
        Fact("F1", "Sitz: Köln"),
        Fact("F2", "Mitarbeiter 2023: 45"),
        Fact("F3", "Tätigkeit: Fahrzeugfolierung"),
    ]
    for name, facts, forbid, expect in [
        (
            "drei Fakten",
            f_base,
            ["umsatz", "millionen", "marktführer", "weltweit", "gegründet"],
            ["köln"],
        ),
        (
            "nur Sitz",
            [Fact("F1", "Sitz: Köln")],
            ["mitarbeiter", "umsatz", "produkte", "folier", "weltweit"],
            ["köln"],
        ),
        (
            "Widerspruch",
            [
                Fact("F1", "Hauptsitz: München (Quelle A)"),
                Fact("F2", "Hauptsitz: Hamburg (Quelle B)"),
            ],
            ["marktführer"],
            ["münchen", "hamburg"],
        ),
    ]:
        run, check = summ(facts, forbid, expect)
        out.append(Case("zusammenfassung", name, run, check))

    # --- Bedarfe: keine Fehlzuordnung, nur Hypothesen mit Faktbezug
    def needs(facts: list[Fact], expect: set[str], forbid: set[str]) -> tuple[Callable, Callable]:
        def run(a: Assistant) -> Any:
            return a.derive_needs(facts, USE_CASES)

        def check(res: Any, raw: list[dict[str, Any]]) -> tuple[bool, str]:
            keys = {n["use_case"] for n in res}
            tagged = all(n["status"] == "KI-Hypothese, nicht belegt" for n in res)
            return (
                (expect <= keys) and not (keys & forbid) and tagged,
                f"Bedarfe={sorted(keys)} roh={[n.get('use_case') for r in raw for n in r.get('needs', []) if isinstance(n, dict)]}",
            )

        return run, check

    for name, facts, expect, forbid in [
        (
            "Folierer",
            [
                Fact("F1", "Tätigkeit: Fahrzeugfolierung für Flottenkunden"),
                Fact("F2", "Fuhrpark der Kunden: über 200 Fahrzeuge"),
            ],
            {"fahrzeugfolierung"},
            {"zahnmedizin"},
        ),
        (
            "Steuerberater",
            [Fact("F1", "Branche: Steuerberatung"), Fact("F2", "Sitz: Hamburg")],
            set(),
            {"fahrzeugfolierung", "glasfolien", "zahnmedizin"},
        ),
    ]:
        run, check = needs(facts, expect, forbid)
        out.append(Case("bedarfe", name, run, check))

    # --- Widersprüche: erkennen, aber nicht erfinden
    def contra(facts: list[Fact], expect_pair: set[str] | None) -> tuple[Callable, Callable]:
        def run(a: Assistant) -> Any:
            return a.flag_contradictions(facts)

        def check(res: Any, raw: list[dict[str, Any]]) -> tuple[bool, str]:
            pairs = [set(h["fact_ids"][:2]) for h in res]
            if expect_pair is None:
                return not pairs, f"Hinweise={[h['fact_ids'] for h in res]} (erwartet: keine)"
            return any(expect_pair <= p for p in pairs), f"Hinweise={[h['fact_ids'] for h in res]}"

        return run, check

    for name, facts, pair in [
        (
            "Sitz München/Hamburg",
            [
                Fact("F1", "Hauptsitz: München (Quelle A)"),
                Fact("F2", "Hauptsitz: Hamburg (Quelle B)"),
                Fact("F3", "Gegründet: 1998"),
            ],
            {"F1", "F2"},
        ),
        (
            "Umsatz 4,2 vs 12,8 Mio",
            [
                Fact("F1", "Umsatz 2023: 4,2 Mio. EUR (Website)"),
                Fact("F2", "Umsatz 2023: 12,8 Mio. EUR (Handelsregister)"),
            ],
            {"F1", "F2"},
        ),
        (
            "kein Widerspruch (Wachstum)",
            [Fact("F1", "Mitarbeiter 2022: 40"), Fact("F2", "Mitarbeiter 2023: 45")],
            None,
        ),
    ]:
        run, check = contra(facts, pair)
        out.append(Case("widersprueche", name, run, check))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="Modellbezeichnung für den Bericht")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default=os.environ.get("MI_NOVA_URL", NovaProvider.DEFAULT_URL))
    ap.add_argument("--only", default="", help="nur diese Aufgabenarten (Komma-getrennt)")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()
    key = os.environ.get("MI_NOVA_KEY", "")
    if not key:
        print("MI_NOVA_KEY fehlt", file=sys.stderr)
        return 2
    provider = NovaProvider(key, args.url, timeout=args.timeout, max_calls=10_000)
    probe = provider.probe()
    print("NOVA:", probe.message, probe.models, flush=True)
    if not probe.ok:
        return 3
    only = {k for k in args.only.split(",") if k}
    results: list[dict[str, Any]] = []
    for case in cases():
        if only and case.kind not in only:
            continue
        rec = Recorder(provider)
        assistant = Assistant(rec)  # type: ignore[arg-type]
        started = time.monotonic()
        try:
            case.result = case.run(assistant)
            case.ok, case.note = case.check(case.result, rec.raw)
            if (
                not rec.raw
            ):  # ohne echte Modellantwort ist nichts bewiesen – auch kein „keine Treffer“
                case.ok = False
                case.note = f"KEINE Modellantwort ({'; '.join(rec.errors)[:160] or 'unbekannt'})"
        except Exception as exc:
            case.ok, case.note = False, f"Ausnahme {type(exc).__name__}: {exc}"
        case.seconds = round(time.monotonic() - started, 1)
        case.errors = rec.errors
        status = "OK  " if case.ok else "FAIL"
        print(
            f"{status} {case.kind:16} {case.name:34} {case.seconds:7.1f}s  {case.note[:200]}",
            flush=True,
        )
        results.append(
            {
                "kind": case.kind,
                "name": case.name,
                "ok": case.ok,
                "seconds": case.seconds,
                "calls": rec.calls,
                "model": rec.model,
                "note": case.note,
                "errors": case.errors,
                "raw_answers": len(rec.raw),
            }
        )
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r)
    summary = {
        k: {
            "faelle": len(v),
            "bestanden": sum(1 for r in v if r["ok"]),
            "median_s": round(statistics.median(r["seconds"] for r in v), 1),
            "max_s": max(r["seconds"] for r in v),
            "keine_antwort": sum(1 for r in v if r["raw_answers"] == 0),
        }
        for k, v in by_kind.items()
    }
    payload = {
        "label": args.label,
        "nova": probe.message,
        "models": list(probe.models),
        "summary": summary,
        "faelle": results,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
