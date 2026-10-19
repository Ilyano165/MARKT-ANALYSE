"""KI-Aufgaben mit deterministischer Prüfung jeder Antwort.

Die KI darf: Suchbegriffe vorschlagen, relevante Passagen markieren, Anwendungsfälle
klassifizieren, Profile aus belegten Fakten zusammenfassen, mögliche Bedarfe ableiten und
Widersprüche markieren. Sie darf nie Kennzahlen als Fakten liefern:

- Passagen/Klassifikationen gelten nur mit wörtlichem, im Text gefundenem Zitat.
- Zusammenfassungen gelten nur, wenn jeder Satz eine Faktreferenz [F…] trägt und jede Zahl
  darin in einem referenzierten Fakt vorkommt.
- Firmennamen aus Suchstrategien werden nur übernommen, wenn sie im Auftragstext stehen.
Ohne KI liefern alle Methoden leere Ergebnisse; die Anwendung arbeitet dann regelbasiert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..extraction.text import parse_number, quote_in_text
from .providers import LLMProvider, NullProvider, wrap_untrusted

MAX_TERM_LEN = 60
MAX_ITEMS = 15


def _strings(value: Any, limit: int = MAX_ITEMS, max_len: int = MAX_TERM_LEN) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        if isinstance(item, str):
            cleaned = re.sub(r"\s+", " ", item).strip()
            if 2 <= len(cleaned) <= max_len and not re.search(r"[<>{}]|https?://", cleaned):
                out.append(cleaned)
    return list(dict.fromkeys(out))


_ARRAY = {"type": "array", "items": {"type": "string"}}


@dataclass(frozen=True, slots=True)
class Quoted:
    """Ergebnis mit geprüftem Zitat."""

    label: str
    quote: str


@dataclass(frozen=True, slots=True)
class Fact:
    """Belegter Fakt für Zusammenfassungen (ID, Text, Quelle)."""

    fact_id: str
    text: str


class Assistant:
    """Fassade über einem Provider."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider: LLMProvider = provider or NullProvider()

    @property
    def name(self) -> str:
        return self.provider.name

    def available(self) -> bool:
        return self.provider.available()

    # ------------------------------------------------------------------ Suchstrategie

    def suggest_strategy(self, task_text: str) -> dict[str, list[str]]:
        """Zusätzliche Suchbegriffe, Branchenbegriffe, Kennzahlen, Firmennamen."""
        schema = {
            "type": "object",
            "properties": {
                "search_terms": _ARRAY,
                "industry_terms": _ARRAY,
                "company_names": _ARRAY,
                "metrics": _ARRAY,
            },
            "required": ["search_terms"],
        }
        data = self.provider.complete_json(
            "Zerlege den Rechercheauftrag in Suchbegriffe (inkl. Synonyme, englische und "
            "landessprachliche Varianten), Branchenbegriffe, ausdrücklich genannte Firmennamen "
            "und gesuchte Kennzahlen.",
            wrap_untrusted(task_text, 2000),
            schema,
        )
        if not data:
            return {}
        names = [
            n for n in _strings(data.get("company_names")) if n.lower() in task_text.lower()
        ]  # nur wörtlich genannte Firmen
        metrics = [
            m
            for m in _strings(data.get("metrics"))
            if m in ("revenue", "headcount_total", "locations", "hiring", "products", "markets")
        ]
        return {
            "search_terms": _strings(data.get("search_terms")),
            "industry_terms": _strings(data.get("industry_terms")),
            "company_names": names,
            "metrics": metrics,
        }

    # ------------------------------------------------------------------ Texte

    def relevant_passages(self, text: str, question: str) -> list[Quoted]:
        """Wörtliche Passagen zu einer Frage; ungeprüfte Zitate werden verworfen."""
        schema = {"type": "object", "properties": {"passages": _ARRAY}, "required": ["passages"]}
        data = self.provider.complete_json(
            f"Zitiere wörtlich bis zu 5 Passagen (je max. 300 Zeichen), die diese Frage "
            f"beantworten: {question}",
            wrap_untrusted(text),
            schema,
        )
        if not data:
            return []
        return [
            Quoted("passage", q)
            for q in _strings(data.get("passages"), 5, 300)
            if quote_in_text(q, text)
        ]

    def classify_use_cases(self, text: str, labels: dict[str, str]) -> list[Quoted]:
        """Anwendungsfälle mit Zitat; nur bekannte Schlüssel und geprüfte Zitate."""
        schema = {
            "type": "object",
            "properties": {
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}, "quote": {"type": "string"}},
                        "required": ["key", "quote"],
                    },
                }
            },
            "required": ["matches"],
        }
        catalog = "\n".join(f"- {k}: {v}" for k, v in labels.items())
        data = self.provider.complete_json(
            "Welche dieser Tätigkeiten übt das Unternehmen laut eigenem Text aus? Nur ausdrücklich "
            f"belegte Treffer, je mit wörtlichem Zitat (max. 200 Zeichen).\n{catalog}",
            wrap_untrusted(text),
            schema,
        )
        if not data or not isinstance(data.get("matches"), list):
            return []
        out = []
        for item in data["matches"][:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            key, quote = str(item.get("key", "")), str(item.get("quote", ""))[:300]
            if key in labels and quote_in_text(quote, text):
                out.append(Quoted(key, quote))
        return out

    # ------------------------------------------------------------------ Berichte

    def summarize(self, facts: list[Fact]) -> str | None:
        """Zusammenfassung nur aus Fakten; jeder Satz mit [F…], jede Zahl belegt."""
        if not facts:
            return None
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        }
        listing = "\n".join(f"[{f.fact_id}] {f.text}" for f in facts)
        data = self.provider.complete_json(
            "Fasse das Unternehmensprofil in höchstens 5 Sätzen zusammen. Verwende nur die "
            "Fakten; jeder Satz endet mit den genutzten Referenzen wie [F1].",
            wrap_untrusted(listing, 8000),
            schema,
        )
        summary = str((data or {}).get("summary", "")).strip()
        return summary if summary and summary_is_grounded(summary, facts) else None

    def derive_needs(self, facts: list[Fact], use_cases: dict[str, str]) -> list[dict[str, Any]]:
        """Mögliche Bedarfe (Hypothesen) mit Faktreferenzen; unbekannte Referenzen → verworfen."""
        if not facts:
            return []
        schema = {
            "type": "object",
            "properties": {
                "needs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "use_case": {"type": "string"},
                            "rationale": {"type": "string"},
                            "fact_ids": _ARRAY,
                        },
                        "required": ["use_case", "rationale", "fact_ids"],
                    },
                }
            },
            "required": ["needs"],
        }
        catalog = "\n".join(f"- {k}: {v}" for k, v in use_cases.items())
        listing = "\n".join(f"[{f.fact_id}] {f.text}" for f in facts)
        data = self.provider.complete_json(
            f"Leite mögliche Bedarfe an diesen Anwendungsfällen ab:\n{catalog}\nNur mit Bezug "
            "auf Fakten-IDs.",
            wrap_untrusted(listing, 8000),
            schema,
        )
        known = {f.fact_id for f in facts}
        out = []
        for item in (data or {}).get("needs", [])[:10]:
            if not isinstance(item, dict):
                continue
            refs = [r for r in _strings(item.get("fact_ids"), 10, 10) if r in known]
            if item.get("use_case") in use_cases and refs:
                out.append(
                    {
                        "use_case": item["use_case"],
                        "fact_ids": refs,
                        "rationale": str(item.get("rationale", ""))[:400],
                        "status": "KI-Hypothese, nicht belegt",
                    }
                )
        return out

    def flag_contradictions(self, facts: list[Fact]) -> list[dict[str, Any]]:
        """Mögliche Widersprüche zwischen Fakten (nur Hinweise, keine Korrektur)."""
        if len(facts) < 2:
            return []
        schema = {
            "type": "object",
            "properties": {
                "contradictions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"fact_ids": _ARRAY, "reason": {"type": "string"}},
                        "required": ["fact_ids", "reason"],
                    },
                }
            },
            "required": ["contradictions"],
        }
        listing = "\n".join(f"[{f.fact_id}] {f.text}" for f in facts)
        data = self.provider.complete_json(
            "Nenne Faktenpaare, die sich widersprechen (z. B. unterschiedlicher Sitz, "
            "unvereinbare Angaben). Keine neuen Fakten.",
            wrap_untrusted(listing, 8000),
            schema,
        )
        known = {f.fact_id for f in facts}
        out = []
        for item in (data or {}).get("contradictions", [])[:10]:
            if not isinstance(item, dict):
                continue
            refs = [r for r in _strings(item.get("fact_ids"), 5, 10) if r in known]
            if len(refs) >= 2:
                out.append({"fact_ids": refs, "reason": str(item.get("reason", ""))[:300]})
        return out


def _numbers(text: str) -> set[Decimal]:
    values = set()
    for raw in re.findall(r"\d[\d.,']*\d|\d", text):
        parsed = parse_number(raw)
        if parsed is not None:
            values.add(parsed.normalize())
    return values


def summary_is_grounded(summary: str, facts: list[Fact]) -> bool:
    """Jeder Satz hat gültige [F…]-Referenzen; jede Zahl steht in einem referenzierten Fakt."""
    by_id = {f.fact_id: f for f in facts}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?\]])\s+", summary) if s.strip()]
    if not sentences:
        return False
    for sentence in sentences:
        refs = re.findall(r"\[(F\d+)\]", sentence)
        if not refs or any(r not in by_id for r in refs):
            return False
        allowed = set().union(*(_numbers(by_id[r].text) for r in refs))
        allowed |= {Decimal(r[1:]) for r in refs}
        body = re.sub(r"\[F\d+\]", "", sentence)
        if not _numbers(body) <= allowed:
            return False
    return True
