"""Ein Bewertungsstand für alle Ansichten: Aktualität, Grundlage, kontrollierte Neubewertung.

Audit P1-4: Die Lead-Liste las gespeicherte Bewertungen (Modell v2), die Opportunity-Seite
rechnete live mit Modell v3 – Avery Dennison stand gleichzeitig mit Fit 81 und Fit 40 da.

Regel jetzt:

* Maßgeblich ist **immer die gespeicherte jüngste Bewertungsrunde** (``latest_assessments``).
  Lead-Liste, Opportunity-Seite und Export lesen dieselben Zeilen.
* Jede Runde speichert ihre **Grundlage** (``details["basis"]``): Modellversion, Prüfsumme von
  Produktmatrix, Anwendungsfällen, Rollenregeln und Modell sowie Stand der Firmendaten
  (Anzahl und jüngste Zeile je Datentabelle). Daraus entsteht ein Fingerabdruck.
* Weicht der Fingerabdruck vom heutigen Stand ab, ist die Bewertung **veraltet** – sie wird
  so angezeigt und nie stillschweigend als aktuell ausgegeben.
* Neu bewertet wird kontrolliert (``refresh``): per Knopf, Kommandozeile oder Auftrag; der
  Anlass steht in ``details["trigger"]``.
* Eine Runde wird nur gespeichert, wenn keine jüngere Runde existiert (``assessed_at`` =
  Zeitpunkt des Datenstands). Ein langsamer, älterer Lauf überschreibt nie einen neueren.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, func, select

from .. import db
from ..products import Catalog
from ..usecases import UseCase
from .assess import latest_assessments
from .model import Model
from .roles import load_roles

# Tabellen, die eine Bewertung nicht beeinflussen (Notizen, Ergebnisse der Bewertung selbst,
# Protokolle der Recherche).
IGNORED_TABLES = frozenset(
    {
        "company_note",
        "opportunity_score",
        "opportunity_assessment",
        "competitor_assessment",
        "run_candidate",
        "investigation_log",
        "validation_issue",
        "data_gap",
        # Kennzahl-Änderungen für den Bericht; entstehen nach der Bewertung (Schritt 8) und
        # gehen nicht in sie ein. profile_change dagegen fließt ins Wachstum ein.
        "change_event",
    }
)
FRESHNESS_LABELS = {
    "aktuell": "aktuell",
    "modell": "veraltet – älteres Bewertungsmodell",
    "regeln": "veraltet – Produktmatrix oder Regeln geändert",
    "daten": "veraltet – neue oder geänderte Firmendaten seit der Bewertung",
    "ohne_grundlage": "veraltet – Bewertung ohne gespeicherte Grundlage (vor Version 0.6)",
    "fehlt": "nicht bewertet",
}


def _company_tables() -> list[Any]:
    return [
        t
        for t in db.metadata.sorted_tables
        if "company_id" in t.c and "id" in t.c and t.name not in IGNORED_TABLES
    ]


def _canonical(value: Any) -> Any:
    """Prozessunabhängige Form: Mengen sortiert, Dataclasses als Felder, Schlüssel geordnet.

    Regression (Phase G, 2026-11-12): ``repr()`` eines ``frozenset`` hängt von der
    Hash-Initialisierung des Python-Prozesses ab – nach jedem Neustart galten alle Bewertungen
    als „veraltet – Regeln geändert“.
    """
    import dataclasses

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _canonical(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str)
        )
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if hasattr(value, "pattern"):  # kompilierte reguläre Ausdrücke
        return f"re:{value.pattern}"
    return value


def rules_digest(cases: dict[str, UseCase], catalog: Catalog, model: Model) -> str:
    """Prüfsumme aller Regeln, die in die Bewertung eingehen (prozessunabhängig)."""
    payload = _canonical(
        {"model": model, "catalog": catalog, "cases": cases, "roles": load_roles()}
    )
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def data_states(engine: Engine, company_ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """Stand der Firmendaten je Unternehmen: {Tabelle: [Anzahl, jüngste ID]} plus Stammdaten."""
    from ..maintenance import company_group

    states: dict[int, dict[str, Any]] = {}
    requested = None if company_ids is None else set(company_ids)
    with engine.connect() as conn:
        if company_ids is not None:  # zusammengeführte Datensätze mitlesen
            company_ids = sorted({m for c in company_ids for m in company_group(conn, c)})
        companies = select(
            db.company.c.id,
            db.company.c.display_name,
            db.company.c.website_domain,
            db.company.c.country_code,
            db.company.c.merged_into_id,
            db.company.c.entity_kind,
        )
        if company_ids is not None:
            companies = companies.where(db.company.c.id.in_(company_ids))
        for row in conn.execute(companies):
            states[int(row.id)] = {
                "company": [
                    row.display_name,
                    row.website_domain,
                    row.country_code,
                    row.merged_into_id,
                ]
            }
        for table in _company_tables():
            query = select(table.c.company_id, func.count(), func.max(table.c.id)).group_by(
                table.c.company_id
            )
            if company_ids is not None:
                query = query.where(table.c.company_id.in_(company_ids))
            for cid, count, newest in conn.execute(query):
                if cid in states:
                    states[int(cid)][table.name] = [int(count), int(newest or 0)]
        # Zusammengeführte Datensätze gehören zum Datenstand der Zielfirma
        merged = conn.execute(
            select(db.company.c.id, db.company.c.merged_into_id).where(
                db.company.c.merged_into_id.is_not(None)
            )
        ).all()
    for child, parent in merged:
        if parent in states and child in states:
            states[int(parent)][f"merged:{child}"] = {
                k: v for k, v in states[int(child)].items() if k != "company" and k[0] != "_"
            }
    if requested is not None:
        states = {k: v for k, v in states.items() if k in requested}
    return states


def fingerprint(model_version: str, digest: str, state: dict[str, Any]) -> str:
    data = {k: v for k, v in state.items() if not k.startswith("_")}  # interne Felder ohne Belang
    payload = json.dumps([model_version, digest, data], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def basis_for(
    engine: Engine,
    company_id: int,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
    *,
    digest: str | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grundlage einer Bewertung (wird mit der Runde gespeichert)."""
    digest = digest or rules_digest(cases, catalog, model)
    if state is None:
        state = data_states(engine, [company_id]).get(company_id, {})
    return {
        "model_version": model.version,
        "rules_digest": digest,
        "fingerprint": fingerprint(model.version, digest, state),
        "data": {k: v for k, v in state.items() if k != "company" and not k.startswith("_")},
        "computed_at": db.utcnow().isoformat(),
    }


@dataclass(frozen=True)
class Freshness:
    company_id: int
    status: str  # Schlüssel aus FRESHNESS_LABELS
    stored_model: str | None
    assessed_at: str | None

    @property
    def current(self) -> bool:
        return self.status == "aktuell"

    @property
    def label(self) -> str:
        text = FRESHNESS_LABELS[self.status]
        if self.status == "modell" and self.stored_model:
            text += f" ({self.stored_model})"
        return text


def freshness(
    engine: Engine,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
    company_ids: list[int] | None = None,
) -> dict[int, Freshness]:
    """Aktualität der gespeicherten Bewertung je Unternehmen (alle nicht zusammengeführten)."""
    digest = rules_digest(cases, catalog, model)
    states = data_states(engine, company_ids)
    stored: dict[int, dict[str, Any]] = {}
    for row in latest_assessments(engine):
        stored.setdefault(row["company_id"], row)
    out: dict[int, Freshness] = {}
    for cid, state in states.items():
        if state["company"][3] is not None:
            continue  # zusammengeführt
        if state.get("_kind") == "public_buyer" and cid not in stored:
            continue  # öffentliche Auftraggeber werden nicht als Leads bewertet
        found = stored.get(cid)
        if found is None:
            out[cid] = Freshness(cid, "fehlt", None, None)
            continue
        row = found
        details = json.loads(row["details"] or "{}")
        basis = details.get("basis") or {}
        assessed = row["assessed_at"].isoformat() if row["assessed_at"] else None
        if row["model_version"] != model.version:
            status = "modell"
        elif not basis:
            status = "ohne_grundlage"
        elif basis.get("rules_digest") != digest:
            status = "regeln"
        elif basis.get("fingerprint") != fingerprint(model.version, digest, state):
            status = "daten"
        else:
            status = "aktuell"
        out[cid] = Freshness(cid, status, row["model_version"], assessed)
    return out


def stale_ids(fresh: dict[int, Freshness], *, include_missing: bool = True) -> list[int]:
    """Neu zu bewertende Firmen: veraltet und (Standard) noch gar nicht bewertet."""
    return sorted(
        cid
        for cid, f in fresh.items()
        if not f.current and (include_missing or f.status != "fehlt")
    )


def refresh(
    engine: Engine,
    cases: dict[str, UseCase],
    catalog: Catalog,
    model: Model,
    company_ids: list[int] | None = None,
    *,
    only_stale: bool = True,
    trigger: str = "manuell",
    run_id: int | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Kontrollierte Neubewertung. Gibt Zahlen zurück (geprüft, neu bewertet, übersprungen)."""
    from .run import assess_and_store

    fresh = freshness(engine, cases, catalog, model, company_ids)
    targets = stale_ids(fresh) if only_stale else sorted(fresh)
    stored = 0
    skipped = 0
    for i, cid in enumerate(targets, 1):
        result = assess_and_store(
            engine, [cid], cases, catalog, model, run_id=run_id, trigger=trigger
        )
        if result:
            stored += 1
        else:
            skipped += 1  # inzwischen neuere Runde vorhanden
        if progress is not None:
            progress(i, len(targets))
    return {
        "checked": len(fresh),
        "stale": len(stale_ids(fresh)),
        "reassessed": stored,
        "skipped_newer_exists": skipped,
        "model_version": model.version,
    }
