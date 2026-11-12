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
from typing import Any

from sqlalchemy import Engine, insert, select

from .. import db, repo
from ..resolution import normalize_domain, normalize_name
from .registry import SOURCE_BY_KEY

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RowOutcome:
    """Ergebnis je CSV-Zeile (für die Prüfung in der Oberfläche)."""

    line: int  # Zeilennummer in der Datei (Kopfzeile = 1)
    name: str
    status: str  # neu | zugeordnet | doppelt | übersprungen
    detail: str
    company_id: int | None


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Ergebnis eines Imports."""

    document_id: int
    companies: tuple[int, ...]
    skipped_rows: int
    rows: tuple[RowOutcome, ...] = ()

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r.status == status)


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
    """CSV-Datei importieren (siehe ``import_company_bytes``)."""
    return import_company_bytes(
        engine,
        path.read_bytes(),
        filename=path.name,
        list_name=list_name,
        source_url=source_url,
        publisher=publisher,
    )


def import_company_bytes(
    engine: Engine,
    content: bytes,
    *,
    filename: str,
    list_name: str,
    source_url: str,
    publisher: str | None,
) -> ImportResult:
    """CSV mit Spalten ``name`` (Pflicht), optional ``country``, ``website``, ``lei``, ``city``.

    Zuordnung zu bestehenden Firmen nur über LEI, Website-Domain (stark) oder dieselbe Zeile
    derselben Liste (erneuter Import); sonst neue Firma. Innerhalb der Datei werden Dubletten
    (gleiche Domain oder gleicher normierter Name im selben Land) derselben Firma zugeordnet.
    Jede Zeile erhält ein nachvollziehbares Ergebnis (``rows``).
    """
    list_name = list_name.strip()
    if not list_name:
        raise ValueError("Bitte einen Listennamen angeben.")
    document_id = store_document(
        engine,
        "user_list",
        content,
        url=source_url or filename,
        title=list_name,
        publisher=publisher,
        published=None,
        content_type="text/csv",
    )
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect: Any = (
            csv.Sniffer().sniff(text[:4096], delimiters=";,\t") if text.strip() else csv.excel
        )
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not header & {"name", "firma", "company"}:
        raise ValueError(
            "Die Datei braucht eine Spalte „name“ (oder „firma“). Gefunden: "
            + (", ".join(sorted(h for h in header if h)) or "keine Kopfzeile")
        )
    ids: list[int] = []
    outcomes: list[RowOutcome] = []
    seen_domain: dict[str, tuple[int, int]] = {}
    seen_name: dict[tuple[str, str | None], tuple[int, int]] = {}
    with engine.begin() as conn:
        for index, row in enumerate(reader):
            line = index + 2
            lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
            name = lowered.get("name") or lowered.get("firma") or lowered.get("company") or ""
            if not name or not normalize_name(name):
                outcomes.append(RowOutcome(line, name, "übersprungen", "kein Firmenname", None))
                continue
            notes: list[str] = []
            raw_country = (lowered.get("country") or lowered.get("land") or "").strip().upper()
            country = raw_country if len(raw_country) == 2 and raw_country.isalpha() else None
            if raw_country and country is None:
                notes.append(f"Land „{raw_country}“ ungültig (ISO-Code erwartet), ignoriert")
            website = lowered.get("website") or lowered.get("web") or ""
            domain = normalize_domain(website) if website else None
            if website and not domain:
                notes.append(f"Website „{website}“ ungültig, ignoriert")
                website = ""
            key = (normalize_name(name), country)
            earlier = (seen_domain.get(domain) if domain else None) or seen_name.get(key)
            if earlier is not None:
                outcomes.append(
                    RowOutcome(
                        line, name, "doppelt", f"wie Zeile {earlier[1]} – derselben Firma "
                        "zugeordnet", earlier[0],
                    )
                )  # fmt: skip
                continue
            identifiers: list[tuple[str, str]] = []
            if lowered.get("lei"):
                identifiers.append(("lei", lowered["lei"].upper()))
            found: int | None = None
            how = ""
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
                how = f"über Website {domain}" if found else ""
            if found is None:  # erneuter Import derselben Liste: gleiche Zeile = gleiche Firma
                again = conn.execute(
                    select(db.list_membership.c.company_id).where(
                        db.list_membership.c.list_name == list_name,
                        db.list_membership.c.row_text == name[:300],
                    )
                ).scalar()
                if again is not None:
                    found, how = int(again), "aus früherem Import dieser Liste"
            created = False
            if found is None:
                found, created = repo.resolve_or_create(
                    conn,
                    name,
                    identifiers,
                    document_id=document_id,
                    country_code=country,
                    website=website or None,
                    website_source="quelle:liste",
                    city=lowered.get("city") or lowered.get("ort"),
                )
                how = "" if created else "über LEI"
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
            if domain:
                seen_domain[domain] = (found, line)
            seen_name[key] = (found, line)
            detail = "; ".join(([how] if how else []) + notes)
            outcomes.append(
                RowOutcome(line, name, "neu" if created else "zugeordnet", detail, found)
            )
            ids.append(found)
    skipped = sum(1 for o in outcomes if o.status == "übersprungen")
    return ImportResult(document_id, tuple(dict.fromkeys(ids)), skipped, tuple(outcomes))
