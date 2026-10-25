"""Belege für einen möglichen Bedarf: Tätigkeiten, Prozesse, Werkzeug-Erwähnungen.

Gesucht wird ausschließlich in gespeicherten Dokumenten, die einem Unternehmen über Belege
zugeordnet sind (eigene Webseite, bereitgestellte Dokumente, Register, Listen). Jeder Treffer
trägt ein wörtliches Zitat, die Quelle und das Abrufdatum. Es werden keine Aussagen erzeugt,
die nicht in einem Dokument stehen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select

from .. import db
from ..extraction.numbers import sentences
from ..intel.manual import ManualEntryError, document_plain_text

OWN_SOURCES = ("website", "user_upload")  # Angaben des Unternehmens selbst
_LINKED_TABLES = (
    "financial_observation",
    "employee_observation",
    "company_profile_fact",
    "company_location",
    "company_market",
    "corporate_event",
    "business_signal",
    "hiring_observation",
    "company_identifier",
    "list_membership",
)


@dataclass(frozen=True, slots=True)
class Document:
    """Dokument eines Unternehmens mit Herkunft."""

    document_id: int
    url: str
    source_key: str
    retrieved_at: datetime
    title: str

    @property
    def own(self) -> bool:
        return self.source_key in OWN_SOURCES


@dataclass(frozen=True, slots=True)
class Hit:
    """Begriff mit Zitat in einem Dokument."""

    term: str
    quote: str
    document: Document
    negated: bool = False
    is_sentence: bool = True  # ganzer Satz (False: Menüpunkt, Titel, Produktname)

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "quote": self.quote,
            "is_sentence": self.is_sentence,
            "document_id": self.document.document_id,
            "url": self.document.url,
            "source_key": self.document.source_key,
            "own_source": self.document.own,
            "retrieved_at": self.document.retrieved_at.isoformat(),
            "negated": self.negated,
        }


def company_documents(engine: Engine, company_id: int) -> list[Document]:
    """Alle Dokumente, die über gespeicherte Belege mit dem Unternehmen verbunden sind."""
    ids: set[int] = set()
    with engine.connect() as conn:
        for name in _LINKED_TABLES:
            table = db.metadata.tables[name]
            ids.update(
                int(r)
                for r in conn.execute(
                    select(table.c.source_document_id).where(
                        table.c.company_id == company_id,
                        table.c.source_document_id.is_not(None),
                    )
                ).scalars()
            )
        fit_evidence: list[int] = []
        for raw in conn.execute(
            select(db.company_product_fit.c.evidence_ids).where(
                db.company_product_fit.c.company_id == company_id
            )
        ).scalars():
            fit_evidence.extend(int(i) for i in json.loads(raw or "[]"))
        if fit_evidence:
            ids.update(
                int(r)
                for r in conn.execute(
                    select(db.evidence.c.source_document_id).where(
                        db.evidence.c.id.in_(fit_evidence)
                    )
                ).scalars()
            )
        rows = conn.execute(
            select(
                db.source_document.c.id,
                db.source_document.c.url,
                db.source_document.c.source_key,
                db.source_document.c.retrieved_at,
                db.source_document.c.title,
            ).where(db.source_document.c.id.in_(sorted(ids)))
        ).all()
    return [Document(int(r.id), r.url, r.source_key, r.retrieved_at, r.title or "") for r in rows]


# Rechtstexte beschreiben keine Tätigkeit. Live-Test 2026-10-23: Der Markenhinweis im Impressum
# („… geschützt. Dies gilt insbesondere für STEINEL Marken, Typenschilder, Firmenlogos und
# Embleme.“) ergab Fit 40 für einen Hersteller ohne jeden Tätigkeitsbeleg.
_LEGAL_PAGE = re.compile(
    r"/(impressum|imprint|legal[-_]?notice|legal|datenschutz\w*|privacy[-_]?\w*|data[-_]protection"
    r"|agb|terms\w*|nutzungsbedingungen|cookie\w*|disclaimer)(/|\.html?$|$|\?)",
    re.IGNORECASE,
)


def legal_page(url: str) -> bool:
    """Impressum, Datenschutz, AGB u. Ä. (nur für Webseiten, nicht für hochgeladene Dateien)."""
    return url.startswith("http") and bool(_LEGAL_PAGE.search(url))


def _texts(engine: Engine, documents: list[Document]) -> list[tuple[Document, str]]:
    out = []
    for document in documents:
        try:
            text = document_plain_text(engine, document.document_id)
        except ManualEntryError:
            continue
        if text.strip():
            out.append((document, text))
    return out


_NEG_BEFORE = re.compile(
    r"(?<![\wäöüß])(kein\w*|nicht|weder|nie|no|not|never|none)(?![\wäöüß])", re.IGNORECASE
)
_NEG_AFTER = re.compile(
    r"(?<![\wäöüß])(nicht(\s+mehr)?\s+(an|aus|durch)|nicht\s+mehr|not\s+offer|no\s+longer)"
    r"(?![\wäöüß])",
    re.IGNORECASE,
)
_NOT_NEGATION = re.compile(r"nicht\s+nur|not\s+only|nicht\s+zuletzt", re.IGNORECASE)
_CLAUSE = re.compile(r"[,;:–—]|\s-\s|\bsondern\b|\baber\b|\bbut\b", re.IGNORECASE)


def negated(sentence: str, start: int, end: int) -> bool:
    """Verneinung im selben Teilsatz: bis zu sechs Wörter davor oder „bieten … nicht an“ danach.

    „nicht nur … sondern auch“ ist keine Verneinung.
    """
    before = _CLAUSE.split(sentence[:start])[-1]
    after = _CLAUSE.split(sentence[end:])[0]
    window = " ".join(before.split()[-6:])
    if _NOT_NEGATION.search(window) or _NOT_NEGATION.search(after):
        return False
    return bool(_NEG_BEFORE.search(window) or _NEG_AFTER.search(after))


@dataclass(frozen=True, slots=True)
class StatementRule:
    """Wann ein Zitat ein ganzer Satz ist (direkter Beleg) und kein Fragment (Menü, Titel)."""

    min_words: int = 6
    end_marks: str = ".!?…"

    def __call__(self, quote: str) -> bool:
        text = quote.strip().rstrip("\"'“”„»«)]").rstrip()
        return len(text.split()) >= self.min_words and bool(text) and text[-1] in self.end_marks


def find_hits(
    texts: list[tuple[Document, str]],
    terms: tuple[str, ...],
    per_document: int = 3,
    statement: StatementRule | None = None,
    prefer: Callable[[Hit], bool] | None = None,
) -> list[Hit]:
    """Treffer je Begriff und Dokument: erster nicht verneinter ganzer Satz, sonst erstes
    nicht verneintes Fragment; sind alle Vorkommen verneint, wird ein verneinter Treffer
    geliefert (zählt nie als Beleg).

    Ganze Sätze gehen vor, weil das erste Vorkommen auf einer Seite meist der Menüpunkt ist
    (Live 2026-10-23, umdasch.com: „Ladenbau“ im Menü vor „Das Ladenbau-Team der Store
    Makers übernahm erneut die Umsetzung …“). Mit ``prefer`` geht unter den Sätzen der erste
    bevorzugte vor (z. B. „das Unternehmen spricht über sich selbst“).
    """
    is_statement = statement or StatementRule()
    hits = []
    for document, text in texts:
        if legal_page(document.url):
            continue
        found = 0
        for term in terms:
            pattern = re.compile(r"(?<![\wäöüß])" + re.escape(term), re.IGNORECASE)
            first_negated: Hit | None = None
            first_fragment: Hit | None = None
            first_sentence: Hit | None = None
            accepted = False
            for sentence in sentences(text):
                match = pattern.search(sentence)
                if match is None:
                    continue
                full = " ".join(sentence.split())
                quote = full[:400]
                if negated(sentence, match.start(), match.end()):
                    first_negated = first_negated or Hit(
                        term, quote, document, True, is_statement(full)
                    )
                    continue
                if not is_statement(full):  # am ungekürzten Satz prüfen
                    first_fragment = first_fragment or Hit(term, quote, document, is_sentence=False)
                    continue
                hit = Hit(term, quote, document)
                if prefer is not None and not prefer(hit):
                    first_sentence = first_sentence or hit
                    continue
                hits.append(hit)
                accepted = True
                break
            if not accepted and first_sentence is not None:
                hits.append(first_sentence)
                accepted = True
            if not accepted and first_fragment is not None:
                hits.append(first_fragment)
                accepted = True
            if not accepted and first_negated is not None:
                hits.append(first_negated)
            found += accepted
            if found >= per_document:
                break
    return hits


class EvidenceIndex:
    """Lädt die Dokumenttexte eines Unternehmens einmal und durchsucht sie."""

    statement: StatementRule = StatementRule()
    prefer: Callable[[Hit], bool] | None = None

    def __init__(
        self, engine: Engine, company_id: int, statement: StatementRule | None = None
    ) -> None:
        self.documents = company_documents(engine, company_id)
        self.texts = _texts(engine, self.documents)
        if statement is not None:
            self.statement = statement

    def all_hits(self, terms: tuple[str, ...]) -> list[Hit]:
        """Treffer; ein wörtlich gleicher Satz auf mehreren Seiten (Navigation, Fußzeile)
        zählt nur einmal – sonst wirkt ein Menüeintrag wie viele unabhängige Belege."""
        seen: set[tuple[str, str, bool]] = set()
        out = []
        for hit in find_hits(
            self.texts,
            terms,
            per_document=len(terms) or 1,
            statement=self.statement,
            prefer=self.prefer,
        ):
            key = (hit.term.lower(), hit.quote.lower(), hit.negated)
            if key not in seen:
                seen.add(key)
                out.append(hit)
        return out

    def hits(self, terms: tuple[str, ...]) -> list[Hit]:
        """Nur nicht verneinte Treffer (Belege)."""
        return [h for h in self.all_hits(terms) if not h.negated]

    def negated_hits(self, terms: tuple[str, ...]) -> list[Hit]:
        """Begriffe, die nur verneint vorkommen („bieten keine Fahrzeugfolierung an“)."""
        return [h for h in self.all_hits(terms) if h.negated]

    @property
    def newest(self) -> datetime | None:
        return max((d.retrieved_at for d in self.documents), default=None)
