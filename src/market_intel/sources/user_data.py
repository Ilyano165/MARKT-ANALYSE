"""Vom Nutzer bereitgestellte Daten: Firmenlisten (CSV) und Dokumente (PDF/HTML/TXT).

Für Messe-Ausstellerlisten, Verbandsverzeichnisse und Jahresabschlüsse aus dem
Unternehmensregister, die nicht automatisiert abgerufen werden dürfen. Original-URL und
Veröffentlichungsdatum werden nur gespeichert, wenn der Nutzer sie angibt – nie geraten.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, insert, select

from .. import db, repo
from ..resolution import normalize_domain, normalize_name
from .registry import SOURCE_BY_KEY

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Ergebnis eines Imports."""

    document_id: int
    companies: tuple[int, ...]
    skipped_rows: int


def store_document(
    engine: Engine,
    source_key: str,
    content: bytes,
    *,
    url: str,
    title: str,
    publisher: str | None,
    published: date | None,
    content_type: str,
) -> int:
    """Bereitgestelltes Dokument als ``source_document`` speichern (gleicher Inhalt einmal)."""
    if source_key not in ("user_upload", "user_list") or source_key not in SOURCE_BY_KEY:
        raise ValueError(f"unzulässige Quelle {source_key}")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Datei größer als 50 MB")
    digest = hashlib.sha256(content).hexdigest()
    with engine.begin() as conn:
        existing = conn.execute(
            select(db.source_document.c.id).where(
                db.source_document.c.source_key == source_key,
                db.source_document.c.url == url,
                db.source_document.c.sha256 == digest,
            )
        ).scalar()
        if existing is not None:
            return int(existing)
        return int(
            conn.execute(
                insert(db.source_document).values(
                    source_key=source_key,
                    url=url,
                    final_url=url,
                    request_body_sha256="",
                    title=title,
                    publisher=publisher,
                    published_at=published,
                    retrieved_at=db.utcnow(),
                    http_status=None,
                    content_type=content_type,
                    sha256=digest,
                    body=content,
                )
            ).inserted_primary_key[0]
        )


def import_company_list(
    engine: Engine, path: Path, *, list_name: str, source_url: str, publisher: str | None
) -> ImportResult:
    """CSV mit Spalten ``name`` (Pflicht), optional ``country``, ``website``, ``lei``, ``city``.

    Zuordnung zu bestehenden Firmen nur über LEI oder Website-Domain (stark); sonst neue Firma.
    """
    content = path.read_bytes()
    document_id = store_document(
        engine,
        "user_list",
        content,
        url=source_url or path.name,
        title=list_name,
        publisher=publisher,
        published=None,
        content_type="text/csv",
    )
    text = content.decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t") if text.strip() else csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    ids: list[int] = []
    skipped = 0
    with engine.begin() as conn:
        for row in rows:
            lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = lowered.get("name") or lowered.get("firma") or lowered.get("company")
            if not name or not normalize_name(name):
                skipped += 1
                continue
            country = (lowered.get("country") or lowered.get("land") or "").upper()[:2] or None
            website = lowered.get("website") or lowered.get("web") or ""
            identifiers: list[tuple[str, str]] = []
            if lowered.get("lei"):
                identifiers.append(("lei", lowered["lei"].upper()))
            domain = normalize_domain(website) if website else None
            if domain:
                identifiers.append(("domain", domain))
                found = repo.find_company_by_identifier(conn, "domain", domain)
                if found is None:  # gleiche Website im Firmenstamm (eindeutig) = dieselbe Firma
                    same = (
                        conn.execute(
                            select(db.company.c.id).where(
                                db.company.c.website_domain == domain,
                                db.company.c.merged_into_id.is_(None),
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if len(same) == 1:
                        found = int(same[0])
                        repo.add_identifier(conn, found, "domain", domain, document_id)
            else:
                found = None
            if found is None:
                found, _ = repo.resolve_or_create(
                    conn,
                    name,
                    identifiers,
                    document_id=document_id,
                    country_code=country,
                    website=website or None,
                    city=lowered.get("city") or lowered.get("ort"),
                )
            repo.add_alias(conn, found, name, f"list:{list_name}"[:30], document_id)
            exists = conn.execute(
                select(db.list_membership.c.id).where(
                    db.list_membership.c.list_name == list_name,
                    db.list_membership.c.company_id == found,
                )
            ).first()
            if exists is None:
                conn.execute(
                    insert(db.list_membership).values(
                        list_name=list_name,
                        company_id=found,
                        source_document_id=document_id,
                        row_text=name[:300],
                        imported_at=db.utcnow(),
                    )
                )
            ids.append(found)
    return ImportResult(document_id, tuple(dict.fromkeys(ids)), skipped)
