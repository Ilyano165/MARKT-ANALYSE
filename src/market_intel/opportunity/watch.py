"""Watchlists: Unternehmen vormerken, Bewertungsstatus setzen – mit vollständiger Historie."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, insert, select

from .. import db

STATUSES = ("vorgemerkt", "in_pruefung", "qualifiziert", "verworfen", "entfernt")
STATUS_LABELS = {
    "vorgemerkt": "vorgemerkt",
    "in_pruefung": "in Prüfung",
    "qualifiziert": "qualifiziert (Vertrieb)",
    "verworfen": "verworfen",
    "entfernt": "von Liste entfernt",
}
DEFAULT_LIST = "Leads"


def ensure_watchlist(engine: Engine, name: str = DEFAULT_LIST, description: str = "") -> int:
    """Watchlist anlegen, falls nötig; gibt die ID zurück."""
    name = name.strip()[:120] or DEFAULT_LIST
    with engine.begin() as conn:
        found = conn.execute(select(db.watchlist.c.id).where(db.watchlist.c.name == name)).scalar()
        if found is not None:
            return int(found)
        return int(
            conn.execute(
                insert(db.watchlist).values(
                    name=name, description=description, created_at=db.utcnow()
                )
            ).inserted_primary_key[0]
        )


def set_status(
    engine: Engine,
    company_id: int,
    status: str,
    *,
    watchlist: str = DEFAULT_LIST,
    reason: str = "",
    author: str = "",
) -> int:
    """Neuen Status eintragen (alte Einträge bleiben als Historie erhalten)."""
    if status not in STATUSES:
        raise ValueError(f"Status {status!r} unbekannt ({', '.join(STATUSES)})")
    list_id = ensure_watchlist(engine, watchlist)
    with engine.begin() as conn:
        if (
            conn.execute(select(db.company.c.id).where(db.company.c.id == company_id)).first()
            is None
        ):
            raise ValueError(f"Unternehmen {company_id} nicht gefunden")
        return int(
            conn.execute(
                insert(db.watchlist_entry).values(
                    watchlist_id=list_id,
                    company_id=company_id,
                    status=status,
                    reason=reason.strip()[:2000],
                    author=author.strip()[:120],
                    created_at=db.utcnow(),
                )
            ).inserted_primary_key[0]
        )


def current_entries(engine: Engine, watchlist: str | None = None) -> list[dict[str, Any]]:
    """Aktueller Status je (Watchlist, Unternehmen); entfernte Einträge ausgeblendet."""
    entry, wl = db.watchlist_entry, db.watchlist
    query = (
        select(entry, wl.c.name.label("watchlist"), db.company.c.display_name)
        .join(wl, wl.c.id == entry.c.watchlist_id)
        .join(db.company, db.company.c.id == entry.c.company_id)
        .order_by(entry.c.id)
    )
    if watchlist:
        query = query.where(wl.c.name == watchlist)
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    with engine.connect() as conn:
        for row in conn.execute(query).mappings():
            latest[(row["watchlist"], row["company_id"])] = dict(row)
    return [r for r in latest.values() if r["status"] != "entfernt"]


def history_of(engine: Engine, company_id: int) -> list[dict[str, Any]]:
    """Alle Statusänderungen eines Unternehmens, neueste zuerst."""
    entry, wl = db.watchlist_entry, db.watchlist
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(entry, wl.c.name.label("watchlist"))
                .join(wl, wl.c.id == entry.c.watchlist_id)
                .where(entry.c.company_id == company_id)
                .order_by(entry.c.id.desc())
            ).mappings()
        ]


def status_of(engine: Engine, company_id: int) -> dict[str, Any] | None:
    """Jüngster Status über alle Watchlists (für Lead-Export)."""
    items = history_of(engine, company_id)
    return items[0] if items and items[0]["status"] != "entfernt" else None


def customer_lists_of(engine: Engine, company_id: int, lists: list[str]) -> list[str]:
    """Bestandskundenlisten, auf denen das Unternehmen steht."""
    if not lists:
        return []
    with engine.connect() as conn:
        return sorted(
            conn.execute(
                select(db.list_membership.c.list_name).where(
                    db.list_membership.c.company_id == company_id,
                    db.list_membership.c.list_name.in_(lists),
                )
            ).scalars()
        )


def excluded_reason(engine: Engine, company_id: int, lists: list[str]) -> str | None:
    """Grund, ein Unternehmen nicht als neuen Kandidaten vorzuschlagen (oder None)."""
    customer = customer_lists_of(engine, company_id, lists)
    if customer:
        return f"Bestandskunde (Liste {', '.join(customer)})"
    status = status_of(engine, company_id)
    if status and status["status"] == "verworfen":
        return (
            f"verworfen am {status['created_at']:%Y-%m-%d}: {status['reason'] or 'ohne Begründung'}"
        )
    return None
