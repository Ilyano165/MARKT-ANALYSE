"""Rollen eines Unternehmens und Belegklassen von Treffern (Phase 6).

Grundsatz: Eine Rolle (Verarbeitung/Installation, Herstellung, Handel) gilt nur als belegt, wenn
ein ganzer Satz aus eigenen Unternehmensangaben sie nennt UND sich auf das Unternehmen bezieht
(wir/unser/Firmenname) oder als Leistungsangabe beginnt („Montage von …“). Jede Rolle wird für
sich belegt; mehrere Rollen sind möglich. Was keine Regel trifft, bleibt „unklar“ – das ist
keine Aussage, dass die Rolle fehlt. Regeln: ``data/roles.toml``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from ..extraction.numbers import sentences
from ..resolution import normalize_name
from .evidence import Document, Hit, StatementRule, legal_page, negated

ROLE_ORDER = ("verarbeiter", "hersteller", "haendler")
HIT_CLASSES = {
    "taetigkeit": "Satz, in dem das Unternehmen die Tätigkeit selbst nennt",
    "produktbezug": "Satz über ein Produkt (Eignung, Eigenschaften, Sortiment)",
    "zielgruppe": "Satz über Verarbeiter als Zielgruppe (Schulung, Netzwerk, Partner)",
    "erwaehnung": "Satz ohne Bezug auf das Unternehmen selbst (z. B. Artikel, Überschrift)",
    "fragment": "Menüpunkt, Kategorie, Produktname oder Überschrift (kein Satz)",
}
MAX_PER_ROLE = 3
MAX_HINTS = 3


@dataclass(frozen=True, slots=True)
class RoleRule:
    """Muster und Ausschlüsse einer Rolle."""

    key: str
    label: str
    patterns: tuple[re.Pattern[str], ...]
    exclude: tuple[re.Pattern[str], ...]


@dataclass(frozen=True, slots=True)
class RoleRules:
    """Alle Regeln aus ``roles.toml``."""

    version: str
    statement: StatementRule
    self_reference: tuple[re.Pattern[str], ...]
    generic_name_words: frozenset[str]
    product_reference: tuple[re.Pattern[str], ...]
    audience: tuple[re.Pattern[str], ...]
    context: tuple[re.Pattern[str], ...]
    roles: dict[str, RoleRule]


def _compile(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def parse_roles(text: str) -> RoleRules:
    """TOML-Text → Regeln (ungültige reguläre Ausdrücke führen zu ``re.error``)."""
    raw = tomllib.loads(text)
    statement = raw.get("statement", {})
    roles = {
        key: RoleRule(
            key,
            str(raw["roles"][key]["label"]),
            _compile(raw["roles"][key]["patterns"]),
            _compile(raw["roles"][key].get("exclude", [])),
        )
        for key in ROLE_ORDER
    }
    return RoleRules(
        version=str(raw.get("meta", {}).get("version", "?")),
        statement=StatementRule(
            int(statement.get("min_words", 6)), str(statement.get("end_marks", ".!?…"))
        ),
        self_reference=_compile(raw["self_reference"]["patterns"]),
        generic_name_words=frozenset(raw["self_reference"].get("generic_name_words", [])),
        product_reference=_compile(raw["product_reference"]["patterns"]),
        audience=_compile(raw["target_group"]["audience"]),
        context=_compile(raw["target_group"]["context"]),
        roles=roles,
    )


@lru_cache(maxsize=1)
def _default_rules() -> RoleRules:
    return parse_roles(
        resources.files("market_intel.data").joinpath("roles.toml").read_text("utf-8")
    )


def load_roles(path: Path | None = None) -> RoleRules:
    """Eigene Datei oder mitgelieferte Regeln."""
    return _default_rules() if path is None else parse_roles(path.read_text("utf-8"))


def _search(patterns: tuple[re.Pattern[str], ...], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match
    return None


def _leading(sentence: str, term: str, rules: RoleRules) -> bool:
    """Leistungsangabe: Satz beginnt mit dem Begriff oder einem Rollenbegriff („Montage von …“)."""
    first = sentence.split()[0] if sentence.split() else ""
    if term and sentence.lower().startswith(term.lower()):
        return True
    return any(_search(r.patterns, first) for r in rules.roles.values())


_NEG_AFTER_CUE = re.compile(
    r"^\W*(\w+\W+){0,2}?(kein\w*|nicht|not|no|never|nie)(?![\wäöüß])", re.IGNORECASE
)
_NOT_ONLY = re.compile(r"\b(nicht nur|not only)\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[,;:–—]|\s-\s|\bsondern\b|\baber\b|\bbut\b", re.IGNORECASE)


def _role_negated(sentence: str, start: int, end: int) -> bool:
    """Verneint vor dem Begriff (wie bei Tätigkeiten) oder bis drei Wörter danach im selben
    Teilsatz („Wir montieren keine Folien“). „nicht nur … sondern auch“ ist keine Verneinung."""
    if negated(sentence, start, end):
        return True
    after = _CLAUSE_END.split(sentence[end:])[0]
    return bool(_NEG_AFTER_CUE.search(after)) and not _NOT_ONLY.search(after)


def hit_class(hit: Hit, rules: RoleRules, tokens: tuple[str, ...] = ()) -> str:
    """Belegklasse eines Treffers (siehe ``HIT_CLASSES``). Nur ``taetigkeit`` ist ein direkter
    Beleg: ganzer Satz, kein Produkt- oder Zielgruppensatz, und das Unternehmen spricht über
    sich selbst (wir/unser/Firmenname) oder nennt die Leistung am Satzanfang."""
    if not hit.is_sentence:
        return "fragment"
    if _search(rules.product_reference, hit.quote):
        return "produktbezug"
    if _search(rules.audience, hit.quote) or _search(rules.context, hit.quote):
        return "zielgruppe"
    if _self_referenced(hit.quote, tokens, rules) or _leading(hit.quote, hit.term, rules):
        return "taetigkeit"
    return "erwaehnung"


def name_tokens(name: str, rules: RoleRules) -> tuple[str, ...]:
    """Unterscheidende Namensbestandteile (ohne Rechtsform und allgemeine Wörter)."""
    return tuple(
        t for t in normalize_name(name).split() if len(t) >= 4 and t not in rules.generic_name_words
    )


def _self_strength(sentence: str, tokens: tuple[str, ...], rules: RoleRules) -> int:
    """2 = wir/unser/„das Unternehmen“, 1 = Firmenname, 0 = kein Selbstbezug."""
    if _search(rules.self_reference, sentence):
        return 2
    lowered = normalize_name(sentence)
    return int(any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", lowered) for t in tokens))


def _self_referenced(sentence: str, tokens: tuple[str, ...], rules: RoleRules) -> bool:
    return _self_strength(sentence, tokens, rules) > 0


@dataclass(frozen=True, slots=True)
class RoleEvidence:
    """Ein Satz, der eine Rolle belegt."""

    role: str
    quote: str
    cue: str
    basis: str  # „Selbstbezug (wir/unser)“, „Selbstbezug (Firmenname)“, „Leistungsangabe …“
    document: Document
    strength: int = 0  # Sortierung: stärkster Beleg zuerst

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "quote": self.quote,
            "cue": self.cue,
            "basis": self.basis,
            "document_id": self.document.document_id,
            "url": self.document.url,
            "retrieved_at": self.document.retrieved_at.isoformat(),
        }


HINT_LABELS = {
    "zielgruppe_verarbeiter": "richtet sich an Verarbeiter, schult oder vermittelt sie "
    "(belegt keine eigene Verarbeitung)",
}


@dataclass(slots=True)
class RoleProfile:
    """Belegte Rollen und Hinweise eines Unternehmens."""

    roles: dict[str, list[RoleEvidence]] = field(default_factory=dict)
    hints: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rules_version: str = ""

    def has(self, role: str) -> bool:
        return bool(self.roles.get(role))

    @property
    def key(self) -> str:
        found = [r for r in ROLE_ORDER if self.has(r)]
        return "unklar" if not found else found[0] if len(found) == 1 else "mehrfach"

    def label(self, rules: RoleRules) -> str:
        found = [rules.roles[r].label for r in ROLE_ORDER if self.has(r)]
        if not found:
            return "unklar (keine Rolle mit Satzbeleg)"
        return found[0] if len(found) == 1 else "mehrere Rollen: " + ", ".join(found)

    def as_dict(self, rules: RoleRules) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label(rules),
            "roles": {
                r: {
                    "label": rules.roles[r].label,
                    "evidenced": self.has(r),
                    "evidence": [e.as_dict() for e in self.roles.get(r, [])],
                }
                for r in ROLE_ORDER
            },
            "hints": [
                {"kind": kind, "label": HINT_LABELS[kind], **item}
                for kind, items in self.hints.items()
                for item in items
            ],
            "rules_version": self.rules_version,
        }


def classify_roles(
    texts: list[tuple[Document, str]], company_name: str, rules: RoleRules | None = None
) -> RoleProfile:
    """Rollen aus eigenen Unternehmensangaben (Website, Uploads); Rechtstexte ausgenommen."""
    rules = rules or load_roles()
    tokens = name_tokens(company_name, rules)
    profile = RoleProfile(rules_version=rules.version)
    seen: set[str] = set()
    for document, text in texts:
        if not document.own or legal_page(document.url):
            continue
        for sentence in sentences(text):
            full = " ".join(sentence.split())
            if full.lower() in seen or not rules.statement(full):
                continue
            seen.add(full.lower())
            strength = _self_strength(full, tokens, rules)
            own = strength > 0
            product = _search(rules.product_reference, full)
            audience = _search(rules.audience, full)
            context = _search(rules.context, full)
            processing_cue = _search(rules.roles["verarbeiter"].patterns, full)
            first_word = len(full.split()[0])
            for role in rules.roles.values():
                match = _search(role.patterns, full)
                if match is None or _search(role.exclude, full):
                    continue
                leading = match.start() < first_word
                if not (own or leading) or _role_negated(full, match.start(), match.end()):
                    continue
                if role.key == "verarbeiter" and (product or audience or context):
                    continue  # Produkteigenschaft oder Verarbeiter als Zielgruppe
                basis = {
                    2: "Selbstbezug (wir/unser/Unternehmen)",
                    1: "Selbstbezug (Firmenname)",
                    0: "Leistungsangabe am Satzanfang",
                }[strength]
                profile.roles.setdefault(role.key, []).append(
                    RoleEvidence(role.key, full[:400], match.group(0), basis, document, strength)
                )
            hints = profile.hints.setdefault("zielgruppe_verarbeiter", [])
            if (audience or (context and processing_cue)) and len(hints) < MAX_HINTS:
                hints.append(
                    {
                        "quote": full[:400],
                        "url": document.url,
                        "document_id": document.document_id,
                        "retrieved_at": document.retrieved_at.isoformat(),
                    }
                )
    profile.hints = {k: v for k, v in profile.hints.items() if v}
    profile.roles = {
        key: sorted(items, key=lambda r: -r.strength)[:MAX_PER_ROLE]  # stabil: Reihenfolge
        for key, items in profile.roles.items()
    }
    return profile
