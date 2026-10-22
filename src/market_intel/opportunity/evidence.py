"""Belege für einen möglichen Bedarf: Tätigkeiten, Prozesse, Werkzeug-Erwähnungen.

Gesucht wird ausschließlich in gespeicherten Dokumenten, die einem Unternehmen über Belege
zugeordnet sind (eigene Webseite, bereitgestellte Dokumente, Register, Listen). Jeder Treffer
trägt ein wörtliches Zitat, die Quelle und das Abrufdatum. Es werden keine Aussagen erzeugt,
die nicht in einem Dokument stehen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select

from .. import db
from ..extraction.text import find_keyword_hits
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "quote": self.quote,
            "document_id": self.document.document_id,
            "url": self.document.url,
            "source_key": self.document.source_key,
            "own_source": self.document.own,
            "retrieved_at": self.document.retrieved_at.isoformat(),
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


def find_hits(
    texts: list[tuple[Document, str]], terms: tuple[str, ...], per_document: int = 3
) -> list[Hit]:
    """Treffer je Begriff (ein Zitat je Begriff und Dokument)."""
    hits = []
    for document, text in texts:
        for hit in find_keyword_hits(text, terms, max_hits=per_document):
            hits.append(Hit(hit.term, hit.quote, document))
    return hits


class EvidenceIndex:
    """Lädt die Dokumenttexte eines Unternehmens einmal und durchsucht sie."""

    def __init__(self, engine: Engine, company_id: int) -> None:
        self.documents = company_documents(engine, company_id)
        self.texts = _texts(engine, self.documents)

    def hits(self, terms: tuple[str, ...]) -> list[Hit]:
        return find_hits(self.texts, terms, per_document=len(terms) or 1)

    @property
    def newest(self) -> datetime | None:
        return max((d.retrieved_at for d in self.documents), default=None)
