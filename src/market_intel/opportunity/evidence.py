"""Belege für einen möglichen Bedarf: Tätigkeiten, Prozesse, Werkzeug-Erwähnungen.

Gesucht wird ausschließlich in gespeicherten Dokumenten, die einem Unternehmen über Belege
zugeordnet sind (eigene Webseite, bereitgestellte Dokumente, Register, Listen). Jeder Treffer
trägt ein wörtliches Zitat, die Quelle und das Abrufdatum. Es werden keine Aussagen erzeugt,
die nicht in einem Dokument stehen.
"""

from __future__ import annotations

import json
import re
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "quote": self.quote,
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


def find_hits(
    texts: list[tuple[Document, str]], terms: tuple[str, ...], per_document: int = 3
) -> list[Hit]:
    """Treffer je Begriff und Dokument: erster nicht verneinter Satz; sind alle Vorkommen
    verneint, wird ein verneinter Treffer geliefert (zählt nie als Beleg)."""
    hits = []
    for document, text in texts:
        found = 0
        for term in terms:
            pattern = re.compile(r"(?<![\wäöüß])" + re.escape(term), re.IGNORECASE)
            first_negated: Hit | None = None
            accepted = False
            for sentence in sentences(text):
                match = pattern.search(sentence)
                if match is None:
                    continue
                quote = " ".join(sentence.split())[:400]
                if negated(sentence, match.start(), match.end()):
                    first_negated = first_negated or Hit(term, quote, document, True)
                    continue
                hits.append(Hit(term, quote, document))
                accepted = True
                break
            if not accepted and first_negated is not None:
                hits.append(first_negated)
            found += accepted
            if found >= per_document:
                break
    return hits


class EvidenceIndex:
    """Lädt die Dokumenttexte eines Unternehmens einmal und durchsucht sie."""

    def __init__(self, engine: Engine, company_id: int) -> None:
        self.documents = company_documents(engine, company_id)
        self.texts = _texts(engine, self.documents)

    def all_hits(self, terms: tuple[str, ...]) -> list[Hit]:
        return find_hits(self.texts, terms, per_document=len(terms) or 1)

    def hits(self, terms: tuple[str, ...]) -> list[Hit]:
        """Nur nicht verneinte Treffer (Belege)."""
        return [h for h in self.all_hits(terms) if not h.negated]

    def negated_hits(self, terms: tuple[str, ...]) -> list[Hit]:
        """Begriffe, die nur verneint vorkommen („bieten keine Fahrzeugfolierung an“)."""
        return [h for h in self.all_hits(terms) if h.negated]

    @property
    def newest(self) -> datetime | None:
        return max((d.retrieved_at for d in self.documents), default=None)
