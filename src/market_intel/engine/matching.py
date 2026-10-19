"""Schritt 5: Unternehmen abgleichen – Dubletten, Tochtergesellschaften, Marken.

Automatisch zusammengeführt wird weiterhin nur über starke Kennungen (beim Speichern).
Dieser Schritt erzeugt Prüfkandidaten (``match_candidate``):

- ``possible_duplicate``: gleiches Land und Namensähnlichkeit ≥ 0,92, oder gleiche
  Website-Domain, ohne widersprechende starke Kennung (verschiedene LEI/Register → keine Dublette)
- ``possible_subsidiary``: normalisierter Name des einen ist Präfix des anderen
  (z. B. „ORAFOL“ / „ORAFOL Europe“) bei unterschiedlichen starken Kennungen
"""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any

from sqlalchemy import Connection, insert, select

from .. import db, repo
from ..resolution import STRONG_SCHEMES, name_similarity, normalize_name

DUPLICATE_THRESHOLD = 0.92


def _conflicting_strong_ids(a: dict[str, str], b: dict[str, str]) -> list[str]:
    return [s for s in STRONG_SCHEMES & a.keys() & b.keys() if a[s] != b[s]]


def classify_pair(
    a: dict[str, Any], b: dict[str, Any], ids_a: dict[str, str], ids_b: dict[str, str]
) -> tuple[str, float, dict[str, Any]] | None:
    """Paar bewerten; ``None`` = kein Prüfkandidat. Reine Funktion (testbar)."""
    conflicts = _conflicting_strong_ids(ids_a, ids_b)
    same_domain = bool(a.get("website_domain")) and a.get("website_domain") == b.get(
        "website_domain"
    )
    similarity = name_similarity(str(a["display_name"]), str(b["display_name"]))
    same_country = a.get("country_code") and a.get("country_code") == b.get("country_code")
    na, nb = normalize_name(str(a["display_name"])), normalize_name(str(b["display_name"]))
    reasons: dict[str, Any] = {"namensaehnlichkeit": similarity}
    if same_domain:
        reasons["gleiche_domain"] = a["website_domain"]
    if conflicts:
        reasons["verschiedene_starke_kennungen"] = conflicts
        prefix = (na.startswith(nb + " ") or nb.startswith(na + " ")) and min(len(na), len(nb)) >= 3
        if prefix or same_domain:
            return "possible_subsidiary", similarity, reasons
        return None
    if same_domain or (same_country and similarity >= DUPLICATE_THRESHOLD):
        return "possible_duplicate", max(similarity, 0.95 if same_domain else 0.0), reasons
    return None


def find_match_candidates(conn: Connection, company_ids: list[int], run_id: int | None) -> int:
    """Paare unter den Firmen des Auftrags (und bekannten Firmen gleicher Domain) prüfen."""
    rows = {cid: repo.company_row(conn, cid) for cid in company_ids}
    ids = {cid: repo.identifiers_of(conn, cid) for cid in company_ids}
    created = 0
    for left, right in combinations(sorted(rows), 2):
        if rows[left]["merged_into_id"] or rows[right]["merged_into_id"]:
            continue
        result = classify_pair(rows[left], rows[right], ids[left], ids[right])
        if result is None:
            continue
        kind, score, reasons = result
        exists = conn.execute(
            select(db.match_candidate.c.id).where(
                db.match_candidate.c.company_a_id == left,
                db.match_candidate.c.company_b_id == right,
                db.match_candidate.c.kind == kind,
            )
        ).first()
        if exists:
            continue
        conn.execute(
            insert(db.match_candidate).values(
                company_a_id=left,
                company_b_id=right,
                score=score,
                kind=kind,
                reasons=json.dumps(reasons, ensure_ascii=False),
                status="open",
                research_run_id=run_id,
                created_at=db.utcnow(),
            )
        )
        created += 1
    return created
