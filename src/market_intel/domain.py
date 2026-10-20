"""Fachbegriffe: Herkunft, Prüfstatus, Zeiträume und Entwürfe für Beobachtungen.

Die Domäne kennt weder Datenbank noch HTTP. Adapter liefern ausschließlich Entwürfe
(``ObservationDraft`` usw.); gespeichert wird nur über ``observations.ingest_*``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ProvenanceClass(StrEnum):
    """Woher eine Angabe stammt (strikte Trennung laut Spezifikation)."""

    OFFICIAL = "official"
    COMPANY_REPORTED = "company_reported"
    THIRD_PARTY = "third_party"
    DERIVED = "derived"
    ESTIMATE = "estimate"


class VerificationStatus(StrEnum):
    """Prüfstatus einer Beobachtung."""

    CONFIRMED = "confirmed"
    REPORTED = "reported"
    ESTIMATED = "estimated"
    CONFLICTING = "conflicting"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Confidence(StrEnum):
    """Einschätzung der Qualität von Quelle und Extraktion."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PeriodType(StrEnum):
    """Art des Bezugszeitraums."""

    FISCAL_YEAR = "fiscal_year"
    CALENDAR_YEAR = "calendar_year"
    POINT_IN_TIME = "point_in_time"
    UNKNOWN = "unknown"


class Scope(StrEnum):
    """Konsolidierungskreis."""

    GROUP = "group"
    SINGLE_ENTITY = "single_entity"
    UNKNOWN = "unknown"


class FetchStatus(StrEnum):
    """Ergebnis eines Quellenaufrufs; jeder Aufruf wird mit Status protokolliert."""

    OK = "ok"
    CACHED = "cached"
    NOT_MODIFIED = "not_modified"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    BLOCKED_POLICY = "blocked_policy"
    BLOCKED_ROBOTS = "blocked_robots"
    BLOCKED_TDM = "blocked_tdm"
    BLOCKED_SSRF = "blocked_ssrf"
    TOO_LARGE = "too_large"

    @property
    def succeeded(self) -> bool:
        """True, wenn Inhalt verfügbar ist."""
        return self in (FetchStatus.OK, FetchStatus.CACHED, FetchStatus.NOT_MODIFIED)


class GapReason(StrEnum):
    """Warum eine gesuchte Angabe fehlt."""

    NOT_FOUND = "not_found"
    NOT_PUBLISHED = "not_published"
    SOURCE_UNAVAILABLE = "source_unavailable"
    ACCESS_RESTRICTED = "access_restricted"
    LEGAL_BLOCKED = "legal_blocked"
    NOT_APPLICABLE = "not_applicable"


# Kennzahlen (Phase 3). Mitarbeiter: Stichtag, Jahresdurchschnitt (§ 267 Abs. 5 HGB:
# Mittel der vier Quartalsstichtage, ohne Auszubildende) und Vollzeitäquivalente sind
# verschiedene Größen und werden nie miteinander verrechnet.
METRIC_LABELS = {
    "revenue": "Umsatz",
    "headcount_total": "Mitarbeiter (Stichtag)",
    "headcount_avg": "Mitarbeiter (Jahresdurchschnitt)",
    "headcount_fte": "Mitarbeiter (Vollzeitäquivalente)",
}
HEADCOUNT_METRICS = ("headcount_total", "headcount_avg", "headcount_fte")
SEGMENT_PATTERN = re.compile(r"(country|site|region|segment):[A-Za-z0-9_.\- ]{1,60}")

# Statusregel aus DATA_MODEL.md §5: Herkunftsklasse → Startstatus.
DEFAULT_STATUS = {
    ProvenanceClass.OFFICIAL: VerificationStatus.CONFIRMED,
    ProvenanceClass.COMPANY_REPORTED: VerificationStatus.REPORTED,
    ProvenanceClass.THIRD_PARTY: VerificationStatus.REPORTED,
    ProvenanceClass.DERIVED: VerificationStatus.REPORTED,
    ProvenanceClass.ESTIMATE: VerificationStatus.ESTIMATED,
}

# Rangfolge für die Ableitung des gültigen Werts (DATA_MODEL.md §6).
PROVENANCE_RANK = {
    ProvenanceClass.OFFICIAL: 0,
    ProvenanceClass.COMPANY_REPORTED: 1,
    ProvenanceClass.THIRD_PARTY: 2,
    ProvenanceClass.DERIVED: 3,
    ProvenanceClass.ESTIMATE: 4,
}


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """Beleg: Fundstelle und wörtliches Zitat im gespeicherten Dokument."""

    document_id: int
    locator: str
    quote: str
    method: str
    extractor_version: str
    quote_verified: bool
    calculation: str = ""


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """Eine belegte Kennzahl, bevor sie gespeichert wird."""

    company_id: int
    kind: str  # "financial" oder "employee"
    metric: str
    value: Decimal
    unit: str
    period_type: PeriodType
    period_start: date | None
    period_end: date
    provenance_class: ProvenanceClass
    confidence: Confidence
    evidence: EvidenceDraft
    currency: str | None = None
    scope: Scope = Scope.UNKNOWN
    methodology: str = ""
    limitations: str = ""
    derived_from: tuple[int, ...] = field(default_factory=tuple)
    segment: str = ""  # z. B. "country:DE"; leer = Gesamtwert

    def __post_init__(self) -> None:
        if self.kind not in ("financial", "employee"):
            raise ValueError(f"Unbekannte Beobachtungsart {self.kind!r}")
        derived = self.provenance_class in (ProvenanceClass.DERIVED, ProvenanceClass.ESTIMATE)
        if derived and not self.methodology:
            raise ValueError("Abgeleitete und geschätzte Werte brauchen eine Methodik")
        if self.kind == "financial" and not self.currency and self.unit != "pct":
            raise ValueError("Geldbeträge brauchen eine Währung")
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("Zeitraumbeginn liegt nach dem Ende")
        if self.segment and not SEGMENT_PATTERN.fullmatch(self.segment):
            raise ValueError(f"Segment {self.segment!r} hat nicht die Form art:wert")
