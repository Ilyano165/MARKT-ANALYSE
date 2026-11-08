"""Datenpflege: Websites, Stammdaten, Firmenidentitäten (Phase D).

Grundsätze:

* Jede manuelle Änderung wird mit Autor, Grund, altem und neuem Wert in ``company_edit``
  protokolliert (nur anhängen). Nichts wird stillschweigend überschrieben.
* Eine fehlende Website bleibt leer, bis ein Mensch sie einträgt – es wird keine Domain aus dem
  Namen geraten. Eine eingetragene Domain wird nur syntaktisch geprüft (kein Abruf).
* Gehört dieselbe Domain schon zu einer anderen Firma, wird nicht gespeichert, sondern auf die
  mögliche Dublette hingewiesen (Entscheidung durch den Menschen).
* Zusammenführen setzt ``merged_into_id``; Beobachtungen bleiben unveränderlich an ihrer
  ursprünglichen Firma gespeichert und werden über ``company_group`` mitgelesen.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import Engine, func, insert, or_, select, update

from . import db, repo
from .resolution import normalize_domain, normalize_name

COUNTRY = re.compile(r"[A-Z]{2}")
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EDITABLE = {"website_domain": "Website", "display_name": "Name", "country_code": "Land"}


class MaintenanceError(ValueError):
    """Ungültige Eingabe oder Konflikt (mit verständlicher Begründung)."""


def _author(author: str) -> str:
    author = author.strip()
    if not author:
        raise MaintenanceError("Bitte einen Autor angeben (Nachvollziehbarkeit).")
    return author[:120]


def _log(
    conn: Any, company_id: int, field: str, old: Any, new: Any, author: str, reason: str
) -> None:
    conn.execute(
        insert(db.company_edit).values(
            company_id=company_id,
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            author=author,
            reason=reason.strip()[:2000],
            created_at=db.utcnow(),
        )
    )


def clean_domain(raw: str) -> str:
    """Eingabe → Domain (``https://www.firma.de/kontakt`` → ``firma.de``) oder Fehler."""
    domain = normalize_domain(raw.strip()) if raw.strip() else None
    if not domain or not DOMAIN.match(domain):
        raise MaintenanceError(f"„{raw}“ ist keine gültige Website-Adresse.")
    return domain


def companies_with_domain(conn: Any, domain: str, exclude: int | None = None) -> list[int]:
    query = select(db.company.c.id).where(
        db.company.c.website_domain == domain, db.company.c.merged_into_id.is_(None)
    )
    if exclude is not None:
        query = query.where(db.company.c.id != exclude)
    return [int(i) for i in conn.execute(query).scalars()]


def set_website(engine: Engine, company_id: int, raw: str, author: str, reason: str = "") -> str:
    """Website eintragen oder ändern. Leere Eingabe entfernt eine falsche Website."""
    author = _author(author)
    with engine.begin() as conn:
        row = repo.company_row(conn, company_id)
        if row.get("merged_into_id"):
            raise MaintenanceError("Firma wurde zusammengeführt – bitte die Zielfirma bearbeiten.")
        old = row["website_domain"]
        if not raw.strip():
            if old is None:
                return ""
            new = None
        else:
            new = clean_domain(raw)
            others = companies_with_domain(conn, new, exclude=company_id)
            if others:
                raise MaintenanceError(
                    f"Die Domain {new} gehört bereits zu Firma {others[0]}. Falls es dieselbe "
                    "Firma ist: unter „Dubletten“ zusammenführen."
                )
        if new == old:
            return new or ""
        conn.execute(
            update(db.company)
            .where(db.company.c.id == company_id)
            .values(website_domain=new, updated_at=db.utcnow())
        )
        if new:
            repo.add_identifier(conn, company_id, "domain", new, None)
        _log(conn, company_id, "website_domain", old, new, author, reason or "manuell gepflegt")
    return new or ""


def set_field(
    engine: Engine, company_id: int, field: str, value: str, author: str, reason: str = ""
) -> None:
    """Name oder Land korrigieren (Website: ``set_website``)."""
    if field not in ("display_name", "country_code"):
        raise MaintenanceError(f"Feld {field!r} ist nicht änderbar")
    author = _author(author)
    value = value.strip()
    if field == "display_name" and not normalize_name(value):
        raise MaintenanceError("Der Name darf nicht leer sein.")
    if field == "country_code":
        value = value.upper()
        if value and not COUNTRY.fullmatch(value):
            raise MaintenanceError("Land bitte als ISO-Code mit zwei Buchstaben (z. B. DE).")
    with engine.begin() as conn:
        old = repo.company_row(conn, company_id)[field]
        new = value or None
        if new == old:
            return
        conn.execute(
            update(db.company)
            .where(db.company.c.id == company_id)
            .values(**{field: new, "updated_at": db.utcnow()})
        )
        if field == "display_name" and new:
            repo.add_alias(conn, company_id, new, "manual", None)
        _log(conn, company_id, field, old, new, author, reason or "manuell korrigiert")


def edits_of(engine: Engine, company_id: int) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(db.company_edit)
                .where(db.company_edit.c.company_id == company_id)
                .order_by(db.company_edit.c.id.desc())
            ).mappings()
        ]


# ---------------------------------------------------------------------- Identitäten


def company_group(conn: Any, company_id: int) -> list[int]:
    """Firma plus alle in sie zusammengeführten Datensätze (rekursiv)."""
    group = [company_id]
    frontier = [company_id]
    while frontier:
        children = [
            int(i)
            for i in conn.execute(
                select(db.company.c.id).where(db.company.c.merged_into_id.in_(frontier))
            ).scalars()
            if int(i) not in group
        ]
        group.extend(children)
        frontier = children
    return group


def merge(engine: Engine, keep_id: int, merge_id: int, author: str, reason: str) -> None:
    """``merge_id`` in ``keep_id`` zusammenführen (nicht rückgängig zu machen ohne Protokoll)."""
    author = _author(author)
    if keep_id == merge_id:
        raise MaintenanceError("Eine Firma kann nicht mit sich selbst zusammengeführt werden.")
    if not reason.strip():
        raise MaintenanceError("Bitte begründen, warum es dieselbe Firma ist.")
    with engine.begin() as conn:
        keep = repo.company_row(conn, keep_id)
        other = repo.company_row(conn, merge_id)
        if keep["merged_into_id"] or other["merged_into_id"]:
            raise MaintenanceError("Eine der Firmen ist bereits zusammengeführt.")
        conn.execute(
            update(db.company)
            .where(db.company.c.id == merge_id)
            .values(merged_into_id=keep_id, updated_at=db.utcnow())
        )
        repo.add_alias(conn, keep_id, other["display_name"], "merged", None)
        if not keep["website_domain"] and other["website_domain"]:
            conn.execute(
                update(db.company)
                .where(db.company.c.id == keep_id)
                .values(website_domain=other["website_domain"], updated_at=db.utcnow())
            )
            _log(conn, keep_id, "website_domain", None, other["website_domain"], author,
                 f"aus zusammengeführter Firma {merge_id} übernommen")  # fmt: skip
        _log(conn, keep_id, "merged", None, merge_id, author, reason)
        _log(conn, merge_id, "merged_into", None, keep_id, author, reason)
        _decide(conn, keep_id, merge_id, "merged")


def mark_distinct(engine: Engine, a: int, b: int, author: str, reason: str = "") -> None:
    """Zwei ähnliche Firmen sind verschieden – Hinweis wird nicht mehr angezeigt."""
    author = _author(author)
    with engine.begin() as conn:
        _decide(conn, a, b, "distinct")
        _log(conn, a, "distinct_from", None, b, author, reason or "verschiedene Firmen")


def _decide(conn: Any, a: int, b: int, status: str) -> None:
    lo, hi = sorted((a, b))
    existing = conn.execute(
        select(db.match_candidate.c.id).where(
            or_(
                (db.match_candidate.c.company_a_id == lo)
                & (db.match_candidate.c.company_b_id == hi),
                (db.match_candidate.c.company_a_id == hi)
                & (db.match_candidate.c.company_b_id == lo),
            ),
            db.match_candidate.c.kind == "possible_duplicate",
        )
    ).first()
    if existing:
        conn.execute(
            update(db.match_candidate)
            .where(db.match_candidate.c.id == existing.id)
            .values(status=status)
        )
    else:
        conn.execute(
            insert(db.match_candidate).values(
                company_a_id=lo,
                company_b_id=hi,
                score=1,
                kind="possible_duplicate",
                reasons="manuell entschieden",
                status=status,
                created_at=db.utcnow(),
            )
        )


def duplicate_groups(engine: Engine, limit: int = 200) -> list[dict[str, Any]]:
    """Mögliche Dubletten: gleiche Domain, gleicher normierter Name, offene Abgleich-Hinweise.

    Bereits entschiedene Paare (verschieden/zusammengeführt) werden nicht mehr gezeigt.
    """
    with engine.connect() as conn:
        companies = {
            int(r.id): dict(r._mapping)
            for r in conn.execute(
                select(
                    db.company.c.id,
                    db.company.c.display_name,
                    db.company.c.country_code,
                    db.company.c.website_domain,
                    db.company.c.entity_kind,
                ).where(db.company.c.merged_into_id.is_(None))
            )
        }
        decided = {
            tuple(sorted((int(r.company_a_id), int(r.company_b_id))))
            for r in conn.execute(
                select(db.match_candidate.c.company_a_id, db.match_candidate.c.company_b_id).where(
                    db.match_candidate.c.status.in_(["distinct", "merged", "rejected"])
                )
            )
        }
        open_hints = [
            (int(r.company_a_id), int(r.company_b_id), r.reasons)
            for r in conn.execute(
                select(
                    db.match_candidate.c.company_a_id,
                    db.match_candidate.c.company_b_id,
                    db.match_candidate.c.reasons,
                ).where(
                    db.match_candidate.c.status == "open",
                    db.match_candidate.c.kind == "possible_duplicate",
                )
            )
        ]
        counts = {
            int(cid): int(n)
            for cid, n in conn.execute(
                select(db.business_signal.c.company_id, func.count()).group_by(
                    db.business_signal.c.company_id
                )
            )
            if cid is not None
        }
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for cid, c in companies.items():
        if c["website_domain"]:
            buckets[("Gleiche Website", c["website_domain"])].append(cid)
        key = normalize_name(c["display_name"])
        if key:
            buckets[("Gleicher Name", f"{key}|{c['country_code'] or ''}")].append(cid)
    groups = []
    seen: set[tuple[int, ...]] = set()
    for (reason, key), ids in buckets.items():
        if len(ids) < 2:
            continue
        pairs = {tuple(sorted((a, b))) for a in ids for b in ids if a < b}
        if pairs <= decided:
            continue
        members = tuple(sorted(ids))
        if members in seen:
            continue
        seen.add(members)
        groups.append(
            {
                "reason": reason,
                "key": key.split("|")[0],
                "companies": [{**companies[i], "signals": counts.get(i, 0)} for i in members],
            }
        )
    for a, b, why in open_hints:
        members = tuple(sorted((a, b)))
        if members in seen or members in decided or a not in companies or b not in companies:
            continue
        seen.add(members)
        groups.append(
            {
                "reason": "Abgleich-Hinweis",
                "key": _reasons_text(why),
                "companies": [{**companies[i], "signals": counts.get(i, 0)} for i in members],
            }
        )
    groups.sort(key=lambda g: (g["reason"] != "Gleiche Website", -len(g["companies"])))
    return groups[:limit]


def _reasons_text(raw: str) -> str:
    """Gespeicherte Abgleich-Gründe (JSON) lesbar machen."""
    import json

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    if not isinstance(data, dict):
        return str(raw)
    labels = {"namensaehnlichkeit": "Namensähnlichkeit"}
    parts = []
    for key, value in data.items():
        if isinstance(value, float):
            value = f"{value:.0%}".replace(".", ",")
        parts.append(f"{labels.get(key, key)} {value}")
    return ", ".join(parts)


def missing_websites(engine: Engine, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Firmen ohne Website (ohne öffentliche Auftraggeber), meistbelegte zuerst."""
    with engine.connect() as conn:
        stmt = (
            select(
                db.company.c.id,
                db.company.c.display_name,
                db.company.c.country_code,
                db.company.c.entity_kind,
            )
            .where(
                db.company.c.merged_into_id.is_(None),
                or_(db.company.c.website_domain.is_(None), db.company.c.website_domain == ""),
                db.company.c.entity_kind != "public_buyer",
            )
            .order_by(db.company.c.display_name)
        )
        if query:
            stmt = stmt.where(func.lower(db.company.c.display_name).like(f"%{query.lower()}%"))
        return [dict(r._mapping) for r in conn.execute(stmt.limit(limit))]
