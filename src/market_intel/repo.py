"""Speichern und Lesen. Einziger Ort, an dem Beobachtungen entstehen (append-only).

Neue Angaben überschreiben nie alte. Gleiche Angabe aus gleicher Quelle → nur
``last_seen_at``. Abweichende Angabe für denselben Schlüssel → beide ``conflicting``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, and_, insert, select, update

from . import db
from .domain import (
    DEFAULT_STATUS,
    PROVENANCE_RANK,
    EvidenceDraft,
    GapReason,
    ObservationDraft,
    ProvenanceClass,
    Scope,
    VerificationStatus,
)
from .resolution import STRONG_SCHEMES, normalize_domain, normalize_name
from .sources.registry import SOURCES, is_enabled

# Toleranzen für Konflikte (DATA_MODEL.md §5)
REL_TOLERANCE = {"revenue": Decimal("0.02"), "headcount_total": Decimal("0.05")}
ABS_TOLERANCE = {"headcount_total": Decimal(10)}


def sync_sources(
    engine: Engine, enabled: tuple[str, ...], disabled: tuple[str, ...], has_ch_key: bool
) -> frozenset[str]:
    """Schreibt das Register in die Datenbank; gibt die aktivierten Quellen zurück."""
    active = set()
    with engine.begin() as conn:
        for s in SOURCES:
            on = is_enabled(s, enabled, disabled, has_ch_key)
            if on:
                active.add(s.key)
            values = {
                "name": s.name,
                "publisher": s.publisher,
                "base_url": s.base_url,
                "source_type": s.source_type,
                "default_provenance": s.default_provenance.value,
                "license": s.license,
                "terms_url": s.terms_url,
                "free_scope": s.free_scope,
                "restrictions": s.restrictions,
                "registration": s.registration,
                "credentials": s.credentials,
                "optional_costs": s.optional_costs,
                "legal_status": s.legal_status,
                "enabled": on,
                "min_interval_seconds": Decimal(str(s.min_interval_seconds)),
            }
            if (
                conn.execute(
                    update(db.source).where(db.source.c.key == s.key).values(**values)
                ).rowcount
                == 0
            ):
                conn.execute(insert(db.source).values(key=s.key, **values))
    return frozenset(active)


# ---------------------------------------------------------------------- Firmen


def find_company_by_identifier(conn: Connection, scheme: str, value: str) -> int | None:
    """Firma mit genau diesem Identifikator (Zusammenführungen werden verfolgt)."""
    row = conn.execute(
        select(db.company_identifier.c.company_id).where(
            db.company_identifier.c.scheme == scheme, db.company_identifier.c.value == value
        )
    ).scalar()
    return None if row is None else _follow_merge(conn, int(row))


def _follow_merge(conn: Connection, company_id: int) -> int:
    seen = set()
    while company_id not in seen:
        seen.add(company_id)
        merged = conn.execute(
            select(db.company.c.merged_into_id).where(db.company.c.id == company_id)
        ).scalar()
        if merged is None:
            return company_id
        company_id = int(merged)
    return company_id


def create_company(
    conn: Connection,
    display_name: str,
    *,
    country_code: str | None = None,
    legal_name: str | None = None,
    legal_form: str | None = None,
    city: str | None = None,
    website: str | None = None,
    status: str = "unknown",
    entity_kind: str = "legal_entity",
) -> int:
    """Legt eine Firma an."""
    now = db.utcnow()
    return int(
        conn.execute(
            insert(db.company).values(
                display_name=display_name,
                legal_name=legal_name,
                legal_form=legal_form,
                country_code=country_code,
                registered_city=city,
                website_domain=normalize_domain(website) if website else None,
                status=status,
                entity_kind=entity_kind,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
    )


def resolve_or_create(
    conn: Connection,
    display_name: str,
    identifiers: Sequence[tuple[str, str]],
    *,
    document_id: int | None,
    **company_fields: Any,
) -> tuple[int, bool]:
    """Firma über starke Identifikatoren finden, sonst neu anlegen. Gibt (id, neu) zurück."""
    for scheme, value in identifiers:
        if scheme in STRONG_SCHEMES:
            found = find_company_by_identifier(conn, scheme, value)
            if found is not None:
                for s, v in identifiers:
                    add_identifier(conn, found, s, v, document_id)
                fill_company_fields(conn, found, **company_fields)
                return found, False
    company_id = create_company(conn, display_name, **company_fields)
    for scheme, value in identifiers:
        add_identifier(conn, company_id, scheme, value, document_id)
    add_alias(conn, company_id, display_name, "display_name", document_id)
    return company_id, True


def fill_company_fields(conn: Connection, company_id: int, **fields: Any) -> None:
    """Füllt nur leere Stammdatenfelder; vorhandene Angaben bleiben unverändert."""
    row = conn.execute(select(db.company).where(db.company.c.id == company_id)).mappings().one()
    mapping = {"city": "registered_city", "website": "website_domain"}
    changes: dict[str, Any] = {}
    for key, value in fields.items():
        column = mapping.get(key, key)
        if value in (None, "") or row[column] not in (None, "", "unknown"):
            continue
        changes[column] = normalize_domain(value) if column == "website_domain" else value
    if changes:
        changes["updated_at"] = db.utcnow()
        conn.execute(update(db.company).where(db.company.c.id == company_id).values(**changes))


def add_identifier(
    conn: Connection, company_id: int, scheme: str, value: str, document_id: int | None
) -> bool:
    """Identifikator speichern. False, wenn er bereits einer anderen Firma gehört."""
    existing = conn.execute(
        select(db.company_identifier.c.company_id).where(
            db.company_identifier.c.scheme == scheme, db.company_identifier.c.value == value
        )
    ).scalar()
    if existing is not None:
        return _follow_merge(conn, int(existing)) == company_id
    conn.execute(
        insert(db.company_identifier).values(
            company_id=company_id,
            scheme=scheme,
            value=value,
            source_document_id=document_id,
            created_at=db.utcnow(),
        )
    )
    return True


def add_alias(
    conn: Connection, company_id: int, alias: str, alias_type: str, document_id: int | None
) -> None:
    """Alias speichern (Duplikate werden ignoriert)."""
    normalized = normalize_name(alias)
    if not normalized:
        return
    exists = conn.execute(
        select(db.company_alias.c.id).where(
            db.company_alias.c.company_id == company_id,
            db.company_alias.c.normalized == normalized,
            db.company_alias.c.alias_type == alias_type,
        )
    ).first()
    if exists is None:
        conn.execute(
            insert(db.company_alias).values(
                company_id=company_id,
                alias=alias,
                normalized=normalized,
                alias_type=alias_type,
                source_document_id=document_id,
            )
        )


def add_location(
    conn: Connection,
    company_id: int,
    location_type: str,
    *,
    country_code: str | None,
    city: str | None,
    postal_code: str | None,
    street: str | None,
    document_id: int,
    provenance: ProvenanceClass,
) -> None:
    """Standort speichern (gleicher Standort nur einmal)."""
    exists = conn.execute(
        select(db.company_location.c.id).where(
            db.company_location.c.company_id == company_id,
            db.company_location.c.location_type == location_type,
            db.company_location.c.country_code == country_code,
            db.company_location.c.city == city,
            db.company_location.c.street == street,
        )
    ).first()
    if exists is None:
        conn.execute(
            insert(db.company_location).values(
                company_id=company_id,
                location_type=location_type,
                country_code=country_code,
                city=city,
                postal_code=postal_code,
                street=street,
                source_document_id=document_id,
                provenance_class=provenance.value,
                captured_at=db.utcnow(),
            )
        )


def add_parent(
    conn: Connection,
    parent_id: int,
    child_id: int,
    relationship_type: str,
    basis: str,
    document_id: int,
    provenance: ProvenanceClass,
) -> None:
    """Mutter-Tochter-Beziehung speichern; Selbstbezug wird abgelehnt."""
    if parent_id == child_id:
        return
    exists = conn.execute(
        select(db.parent_company_relationship.c.id).where(
            db.parent_company_relationship.c.parent_company_id == parent_id,
            db.parent_company_relationship.c.child_company_id == child_id,
            db.parent_company_relationship.c.relationship_type == relationship_type,
        )
    ).first()
    if exists is None:
        conn.execute(
            insert(db.parent_company_relationship).values(
                parent_company_id=parent_id,
                child_company_id=child_id,
                relationship_type=relationship_type,
                basis=basis,
                source_document_id=document_id,
                provenance_class=provenance.value,
                captured_at=db.utcnow(),
            )
        )


def add_industry(
    conn: Connection,
    company_id: int,
    scheme: str,
    code: str,
    label: str,
    document_id: int,
    provenance: ProvenanceClass,
) -> None:
    """Branchenzuordnung speichern."""
    industry_id = conn.execute(
        select(db.industry.c.id).where(db.industry.c.scheme == scheme, db.industry.c.code == code)
    ).scalar()
    if industry_id is None:
        industry_id = conn.execute(
            insert(db.industry).values(scheme=scheme, code=code, label=label)
        ).inserted_primary_key[0]
    exists = conn.execute(
        select(db.company_industry.c.id).where(
            db.company_industry.c.company_id == company_id,
            db.company_industry.c.industry_id == industry_id,
        )
    ).first()
    if exists is None:
        conn.execute(
            insert(db.company_industry).values(
                company_id=company_id,
                industry_id=industry_id,
                source_document_id=document_id,
                provenance_class=provenance.value,
                captured_at=db.utcnow(),
            )
        )


# ---------------------------------------------------------------------- Belege, Beobachtungen


def add_evidence(conn: Connection, draft: EvidenceDraft) -> int:
    """Beleg speichern (unveränderlich)."""
    return int(
        conn.execute(
            insert(db.evidence).values(
                source_document_id=draft.document_id,
                locator=draft.locator[:2000],
                quote=draft.quote[:1000],
                extraction_method=draft.method,
                extractor_version=draft.extractor_version,
                quote_verified=draft.quote_verified,
                calculation=draft.calculation,
            )
        ).inserted_primary_key[0]
    )


def _content_hash(draft: ObservationDraft) -> str:
    key = "|".join(
        str(part)
        for part in (
            draft.company_id,
            draft.kind,
            draft.metric,
            draft.period_start,
            draft.period_end,
            draft.scope.value,
            format(draft.value.normalize(), "f"),
            draft.unit,
            draft.currency,
            draft.evidence.document_id,
            draft.evidence.locator,
            draft.provenance_class.value,
        )
    )
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Ergebnis der Aufnahme einer Beobachtung."""

    observation_id: int
    created: bool
    conflict_with: tuple[int, ...]


def ingest_observation(
    conn: Connection, draft: ObservationDraft, run_id: int | None
) -> IngestResult:
    """Speichert eine Beobachtung append-only, erkennt Duplikate und Konflikte."""
    table = db.OBSERVATION_TABLES[draft.kind]
    content_hash = _content_hash(draft)
    now = db.utcnow()
    existing = conn.execute(select(table.c.id).where(table.c.content_hash == content_hash)).scalar()
    if existing is not None:
        conn.execute(update(table).where(table.c.id == existing).values(last_seen_at=now))
        return IngestResult(int(existing), False, ())
    if not draft.evidence.quote_verified:
        raise ValueError("Beobachtung ohne verifizierten Beleg wird nicht gespeichert")
    evidence_id = add_evidence(conn, draft.evidence)
    status = DEFAULT_STATUS[draft.provenance_class]
    observation_id = int(
        conn.execute(
            insert(table).values(
                company_id=draft.company_id,
                metric=draft.metric,
                value=draft.value,
                unit=draft.unit,
                currency=draft.currency,
                period_type=draft.period_type.value,
                period_start=draft.period_start,
                period_end=draft.period_end,
                consolidation_scope=draft.scope.value,
                provenance_class=draft.provenance_class.value,
                verification_status=status.value,
                confidence=draft.confidence.value,
                source_document_id=draft.evidence.document_id,
                evidence_id=evidence_id,
                methodology=draft.methodology,
                limitations=draft.limitations,
                derived_from=",".join(str(i) for i in draft.derived_from),
                captured_at=now,
                last_seen_at=now,
                research_run_id=run_id,
                content_hash=content_hash,
            )
        ).inserted_primary_key[0]
    )
    conflicts = _detect_conflicts(conn, table, draft, observation_id)
    return IngestResult(observation_id, True, conflicts)


def _detect_conflicts(
    conn: Connection, table: Any, draft: ObservationDraft, new_id: int
) -> tuple[int, ...]:
    if draft.provenance_class in (ProvenanceClass.DERIVED, ProvenanceClass.ESTIMATE):
        return ()
    rows = conn.execute(
        select(
            table.c.id,
            table.c.value,
            table.c.unit,
            table.c.currency,
            table.c.consolidation_scope,
        ).where(
            table.c.company_id == draft.company_id,
            table.c.metric == draft.metric,
            table.c.period_end == draft.period_end,
            table.c.id != new_id,
            table.c.provenance_class.notin_(
                [ProvenanceClass.DERIVED.value, ProvenanceClass.ESTIMATE.value]
            ),
            table.c.verification_status.notin_(
                [VerificationStatus.REJECTED.value, VerificationStatus.SUPERSEDED.value]
            ),
        )
    ).all()
    conflicting = []
    for row in rows:
        if not _scopes_comparable(row.consolidation_scope, draft.scope.value):
            continue
        if row.unit != draft.unit or row.currency != draft.currency:
            continue
        if not _within_tolerance(draft.metric, Decimal(row.value), draft.value):
            conflicting.append(int(row.id))
    if conflicting:
        conn.execute(
            update(table)
            .where(table.c.id.in_([*conflicting, new_id]))
            .values(verification_status=VerificationStatus.CONFLICTING.value)
        )
    return tuple(conflicting)


def _scopes_comparable(a: str, b: str) -> bool:
    """Unbekannter Konsolidierungskreis ist mit jedem vergleichbar (sonst blieben
    Widersprüche zwischen Konzernwert und Wert ohne Scope-Angabe unsichtbar)."""
    return a == b or Scope.UNKNOWN.value in (a, b)


def _within_tolerance(metric: str, a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    rel = REL_TOLERANCE.get(metric, Decimal("0.01"))
    absolute = ABS_TOLERANCE.get(metric, Decimal(0))
    diff = abs(a - b)
    base = max(abs(a), abs(b))
    return diff <= absolute or (base > 0 and diff / base <= rel)


def add_signal(
    conn: Connection,
    *,
    company_id: int | None,
    signal_type: str,
    event_date: date | None,
    summary: str,
    country_code: str | None,
    use_case_key: str | None,
    evidence: EvidenceDraft,
    url: str | None,
    provenance: ProvenanceClass,
    confidence: str,
    run_id: int | None,
) -> int | None:
    """Signal speichern; dieselbe Meldung (Quelle+URL+Firma+Anwendungsfall) nur einmal."""
    dedup = hashlib.sha256(
        f"{signal_type}|{url}|{company_id}|{use_case_key}|{summary[:200]}".encode()
    ).hexdigest()
    if conn.execute(
        select(db.business_signal.c.id).where(db.business_signal.c.dedup_key == dedup)
    ).first():
        return None
    evidence_id = add_evidence(conn, evidence)
    return int(
        conn.execute(
            insert(db.business_signal).values(
                company_id=company_id,
                signal_type=signal_type,
                event_date=event_date,
                summary=summary[:500],
                country_code=country_code,
                use_case_key=use_case_key,
                source_document_id=evidence.document_id,
                evidence_id=evidence_id,
                url=url,
                provenance_class=provenance.value,
                verification_status=DEFAULT_STATUS[provenance].value,
                confidence=confidence,
                captured_at=db.utcnow(),
                research_run_id=run_id,
                dedup_key=dedup,
            )
        ).inserted_primary_key[0]
    )


def add_gap(
    conn: Connection,
    company_id: int,
    metric: str,
    searched: Iterable[str],
    reason: GapReason,
    detail: str,
    run_id: int | None,
) -> None:
    """Datenlücke dokumentieren (gesucht, nicht gefunden)."""
    conn.execute(
        insert(db.data_gap).values(
            company_id=company_id,
            metric=metric,
            searched_sources=",".join(sorted(set(searched))),
            reason=reason.value,
            detail=detail[:1000],
            research_run_id=run_id,
            checked_at=db.utcnow(),
        )
    )


# ---------------------------------------------------------------------- Lesen


def observations(
    conn: Connection, company_id: int, kind: str, metric: str | None = None
) -> list[dict[str, Any]]:
    """Alle Beobachtungen mit Quelle und Beleg (Historie, nichts ausgeblendet)."""
    table = db.OBSERVATION_TABLES[kind]
    query = (
        select(
            table,
            db.evidence.c.quote,
            db.evidence.c.locator,
            db.evidence.c.extraction_method,
            db.source_document.c.url.label("source_url"),
            db.source_document.c.source_key,
            db.source_document.c.retrieved_at,
            db.source_document.c.publisher,
        )
        .join(db.evidence, db.evidence.c.id == table.c.evidence_id)
        .join(db.source_document, db.source_document.c.id == table.c.source_document_id)
        .where(table.c.company_id == company_id)
        .order_by(table.c.metric, table.c.period_end)
    )
    if metric:
        query = query.where(table.c.metric == metric)
    return [dict(r) for r in conn.execute(query).mappings()]


def current_values(
    conn: Connection, company_id: int, kind: str, metric: str
) -> list[dict[str, Any]]:
    """Gültiger Wert je Zeitraum nach DATA_MODEL.md §6 (mit Konfliktkennzeichen)."""
    by_period: dict[tuple[date, str, str | None], list[dict[str, Any]]] = {}
    for row in observations(conn, company_id, kind, metric):
        if row["verification_status"] in (
            VerificationStatus.REJECTED.value,
            VerificationStatus.SUPERSEDED.value,
        ):
            continue
        key = (row["period_end"], row["consolidation_scope"], row["currency"])
        by_period.setdefault(key, []).append(row)
    # Werte ohne Scope-Angabe gehören zur einzigen bekannten Gruppe desselben Zeitraums.
    for key in [k for k in by_period if k[1] == Scope.UNKNOWN.value]:
        known = [
            k
            for k in by_period
            if k[0] == key[0] and k[2] == key[2] and k[1] != Scope.UNKNOWN.value
        ]
        if len(known) == 1:
            by_period[known[0]].extend(by_period.pop(key))
    result = []
    for rows in by_period.values():
        rows.sort(
            key=lambda r: (
                PROVENANCE_RANK[ProvenanceClass(r["provenance_class"])],
                -r["retrieved_at"].timestamp(),
                -r["captured_at"].timestamp(),
            )
        )
        best = dict(rows[0])
        best["alternatives"] = len(rows) - 1
        best["conflict"] = any(r["verification_status"] == "conflicting" for r in rows)
        result.append(best)
    result.sort(key=lambda r: r["period_end"])
    return result


def company_row(conn: Connection, company_id: int) -> dict[str, Any]:
    """Stammdaten einer Firma."""
    return dict(
        conn.execute(select(db.company).where(db.company.c.id == company_id)).mappings().one()
    )


def identifiers_of(conn: Connection, company_id: int) -> dict[str, str]:
    """Identifikatoren je Schema."""
    rows = conn.execute(
        select(db.company_identifier.c.scheme, db.company_identifier.c.value).where(
            db.company_identifier.c.company_id == company_id
        )
    ).all()
    return {r.scheme: r.value for r in rows}


def gaps_of(conn: Connection, company_id: int) -> list[dict[str, Any]]:
    """Dokumentierte Datenlücken."""
    return [
        dict(r)
        for r in conn.execute(
            select(db.data_gap)
            .where(db.data_gap.c.company_id == company_id)
            .order_by(db.data_gap.c.metric)
        ).mappings()
    ]


def json_dumps(value: Any) -> str:
    """Stabile JSON-Darstellung."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def signals_for(conn: Connection, company_id: int) -> list[dict[str, Any]]:
    """Signale einer Firma, neueste zuerst."""
    return [
        dict(r)
        for r in conn.execute(
            select(db.business_signal, db.evidence.c.quote)
            .join(db.evidence, db.evidence.c.id == db.business_signal.c.evidence_id)
            .where(and_(db.business_signal.c.company_id == company_id))
            .order_by(db.business_signal.c.event_date.desc())
        ).mappings()
    ]
