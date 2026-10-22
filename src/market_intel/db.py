"""Datenbankschema (SQLAlchemy Core) für SQLite und PostgreSQL.

Beobachtungen sind append-only: Trigger verhindern das Ändern von Werten und das Löschen.
Nur Prüfstatus und ``last_seen_at`` dürfen sich ändern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.types import TypeDecorator

SCHEMA_VERSION = 4


class DecimalText(TypeDecorator[Decimal]):
    """Exakte Dezimalzahl; in SQLite als Text, in PostgreSQL als NUMERIC (nie float)."""

    impl = String(48)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        return None if value is None else format(Decimal(value), "f")

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))


def _decimal() -> TypeDecorator[Decimal]:
    return DecimalText().with_variant(Numeric(24, 6), "postgresql")


def utcnow() -> datetime:
    """Aktueller Zeitpunkt in UTC."""
    return datetime.now(UTC)


metadata = MetaData()

source = Table(
    "source",
    metadata,
    Column("key", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("publisher", Text, nullable=False),
    Column("base_url", Text, nullable=False),
    Column("source_type", String(40), nullable=False),
    Column("default_provenance", String(30), nullable=False),
    Column("license", Text, nullable=False),
    Column("terms_url", Text, nullable=False),
    Column("free_scope", Text, nullable=False),
    Column("restrictions", Text, nullable=False),
    Column("registration", Text, nullable=False),
    Column("credentials", Text, nullable=False),
    Column("optional_costs", Text, nullable=False),
    Column("legal_status", String(30), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("min_interval_seconds", Numeric(8, 2), nullable=False),
)

source_document = Table(
    "source_document",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_key", String(40), ForeignKey("source.key"), nullable=False),
    Column("url", Text, nullable=False),
    Column("final_url", Text),
    Column("request_body_sha256", String(64), nullable=False, default=""),
    Column("title", Text),
    Column("publisher", Text),
    Column("published_at", Date),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("http_status", Integer),
    Column("content_type", Text),
    Column("sha256", String(64), nullable=False),
    Column("body", LargeBinary),
    Column("text", Text),
    Column("run_id", Integer),
    UniqueConstraint("source_key", "url", "request_body_sha256", "sha256"),
)

fetch_log = Table(
    "fetch_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer),
    Column("source_key", String(40), nullable=False),
    Column("method", String(8), nullable=False),
    Column("url", Text, nullable=False),
    Column("purpose", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("http_status", Integer),
    Column("error", Text),
    Column("attempt", Integer, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("document_id", Integer),
    Column("at", DateTime(timezone=True), nullable=False),
)

http_cache = Table(
    "http_cache",
    metadata,
    Column("cache_key", String(64), primary_key=True),
    Column("document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("etag", Text),
    Column("last_modified", Text),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

company = Table(
    "company",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("legal_name", Text),
    Column("legal_form", Text),
    Column("country_code", String(2)),
    Column("registered_city", Text),
    Column("website_domain", Text),
    Column("status", String(20), nullable=False, default="unknown"),
    Column("entity_kind", String(30), nullable=False, default="legal_entity"),
    Column("merged_into_id", Integer, ForeignKey("company.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

company_identifier = Table(
    "company_identifier",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("scheme", String(30), nullable=False),
    Column("value", Text, nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("scheme", "value"),
)

company_alias = Table(
    "company_alias",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("alias", Text, nullable=False),
    Column("normalized", Text, nullable=False),
    Column("alias_type", String(30), nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id")),
    UniqueConstraint("company_id", "normalized", "alias_type"),
)

parent_company_relationship = Table(
    "parent_company_relationship",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("parent_company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("child_company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("relationship_type", String(30), nullable=False),
    Column("basis", String(40), nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("parent_company_id", "child_company_id", "relationship_type"),
)

industry = Table(
    "industry",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scheme", String(20), nullable=False),
    Column("code", Text, nullable=False),
    Column("label", Text, nullable=False),
    UniqueConstraint("scheme", "code"),
)

company_industry = Table(
    "company_industry",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("industry_id", Integer, ForeignKey("industry.id"), nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("company_id", "industry_id"),
)

company_location = Table(
    "company_location",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("location_type", String(30), nullable=False),
    Column("country_code", String(2)),
    Column("city", Text),
    Column("postal_code", Text),
    Column("street", Text),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    # Phase 3: Koordinaten nur aus Quellen, mit Genauigkeit (site | city | region | country)
    Column("latitude", _decimal()),
    Column("longitude", _decimal()),
    Column("geo_precision", String(20), nullable=False, default="none"),
    Column("geo_note", Text, nullable=False, default=""),
    Column("site_status", String(20), nullable=False, default="active"),
    Column("evidence_id", Integer, ForeignKey("evidence.id")),
    Column("research_run_id", Integer),
    UniqueConstraint("company_id", "location_type", "country_code", "city", "street"),
)

evidence = Table(
    "evidence",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("locator", Text, nullable=False),
    Column("quote", Text, nullable=False),
    Column("extraction_method", String(30), nullable=False),
    Column("extractor_version", String(60), nullable=False),
    Column("quote_verified", Boolean, nullable=False),
    Column("calculation", Text, nullable=False, default=""),
)


def _observation_table(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
        Column("metric", String(40), nullable=False),
        Column("value", _decimal(), nullable=False),
        Column("unit", String(20), nullable=False),
        Column("currency", String(3)),
        Column("period_type", String(20), nullable=False),
        Column("period_start", Date),
        Column("period_end", Date, nullable=False),
        Column("consolidation_scope", String(20), nullable=False),
        Column("provenance_class", String(30), nullable=False),
        Column("verification_status", String(20), nullable=False),
        Column("confidence", String(10), nullable=False),
        Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
        Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
        Column("methodology", Text, nullable=False, default=""),
        Column("limitations", Text, nullable=False, default=""),
        Column("derived_from", Text, nullable=False, default=""),
        Column("captured_at", DateTime(timezone=True), nullable=False),
        Column("last_seen_at", DateTime(timezone=True), nullable=False),
        Column("research_run_id", Integer),
        Column("supersedes_id", Integer),
        Column("content_hash", String(64), nullable=False, unique=True),
        # Teilmenge, z. B. "country:DE" oder "site:12"; leer = Gesamtwert der Einheit.
        Column("segment", String(80), nullable=False, default=""),
    )


financial_observation = _observation_table("financial_observation")
employee_observation = _observation_table("employee_observation")
OBSERVATION_TABLES = {"financial": financial_observation, "employee": employee_observation}

business_signal = Table(
    "business_signal",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id")),
    Column("signal_type", String(40), nullable=False),
    Column("event_date", Date),
    Column("summary", Text, nullable=False),
    Column("country_code", String(2)),
    Column("use_case_key", String(60)),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
    Column("url", Text),
    Column("provenance_class", String(30), nullable=False),
    Column("verification_status", String(20), nullable=False),
    Column("confidence", String(10), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("research_run_id", Integer),
    Column("dedup_key", String(64), nullable=False, unique=True),
)

data_gap = Table(
    "data_gap",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("metric", String(40), nullable=False),
    Column("searched_sources", Text, nullable=False),
    Column("reason", String(30), nullable=False),
    Column("detail", Text, nullable=False, default=""),
    Column("research_run_id", Integer),
    Column("checked_at", DateTime(timezone=True), nullable=False),
)

research_run = Table(
    "research_run",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_type", String(40), nullable=False),
    Column("parameters", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("summary", Text, nullable=False, default="{}"),
)

research_task = Table(
    "research_task",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_run.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("task_type", String(40), nullable=False),
    Column("step", Integer, nullable=False, default=0),
    Column("label", Text, nullable=False, default=""),
    Column("params", Text, nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("error", Text),
    Column("result", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("run_id", "idempotency_key"),
)

product_use_case = Table(
    "product_use_case",
    metadata,
    Column("key", String(60), primary_key=True),
    Column("name", Text, nullable=False),
    Column("product_categories", Text, nullable=False),
    Column("evidence_status", String(30), nullable=False),
    Column("definition", Text, nullable=False),
)

company_product_fit = Table(
    "company_product_fit",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("use_case_key", String(60), ForeignKey("product_use_case.key"), nullable=False),
    Column("fit_level", String(20), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("evidence_ids", Text, nullable=False),
    Column("assessed_by", Text, nullable=False),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
    Column("research_run_id", Integer),
)

opportunity_assessment = Table(
    "opportunity_assessment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("use_case_key", String(60), nullable=False),
    Column("scoring_model_version", String(20), nullable=False),
    Column("total_score", _decimal(), nullable=False),
    Column("components", Text, nullable=False),
    Column("data_completeness_pct", Integer, nullable=False),
    Column("priority", String(20), nullable=False),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
    Column("research_run_id", Integer),
)


# ---------------------------------------------------------------------- Phase 2: Research Engine

run_candidate = Table(
    "run_candidate",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("research_run.id"), nullable=False),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("adapter_key", String(40), nullable=False),
    Column("role", String(20), nullable=False),  # named | candidate
    Column("reason", Text, nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id")),
    Column("selected", Boolean, nullable=False, default=False),
    Column("found_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "company_id", "adapter_key", "reason"),
)

investigation_log = Table(
    "investigation_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("adapter_key", String(40), nullable=False),
    Column("run_id", Integer),
    Column("status", String(20), nullable=False),  # ok | failed | skipped | not_applicable
    Column("detail", Text, nullable=False, default=""),
    Column("finished_at", DateTime(timezone=True), nullable=False),
)

hiring_observation = Table(
    "hiring_observation",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("observation_date", Date, nullable=False),
    Column("open_positions_count", Integer, nullable=False),
    Column("job_titles", Text, nullable=False),
    Column("locations", Text, nullable=False),
    Column("relevant_use_cases", Text, nullable=False, default="[]"),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
    Column("method", String(40), nullable=False),
    Column("research_run_id", Integer),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("company_id", "source_document_id"),
)

market_statistic = Table(
    "market_statistic",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_key", String(40), nullable=False),
    Column("dataset", String(80), nullable=False),
    Column("indicator", Text, nullable=False),
    Column("dimensions", Text, nullable=False),
    Column("period", String(20), nullable=False),
    Column("value", _decimal(), nullable=False),
    Column("unit", Text, nullable=False, default=""),
    Column("status_flag", String(10)),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_document_id", "dimensions", "period"),
)

match_candidate = Table(
    "match_candidate",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_a_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("company_b_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("score", Numeric(6, 4), nullable=False),
    Column("kind", String(30), nullable=False),  # possible_duplicate | possible_subsidiary
    Column("reasons", Text, nullable=False),
    Column("status", String(20), nullable=False, default="open"),
    Column("research_run_id", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("company_a_id", "company_b_id", "kind"),
)

validation_issue = Table(
    "validation_issue",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("research_run_id", Integer),
    Column("company_id", Integer),
    Column("severity", String(10), nullable=False),  # error | warning | hint
    Column("code", String(60), nullable=False),
    Column("message", Text, nullable=False),
    Column("source_document_id", Integer),
    Column("quote", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

change_event = Table(
    "change_event",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("metric", String(40), nullable=False),
    Column("period_end", Date),
    Column("old_value", _decimal()),
    Column("new_value", _decimal()),
    Column("unit", String(20)),
    Column("change_kind", String(30), nullable=False),  # new_period | changed_value | first_value
    Column("observation_id", Integer, nullable=False),
    Column("research_run_id", Integer),
    Column("detected_at", DateTime(timezone=True), nullable=False),
)

list_membership = Table(
    "list_membership",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("list_name", String(120), nullable=False),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("row_text", Text, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("list_name", "company_id"),
)

# ---------------------------------------------------------------- Phase 3: Company Intelligence

company_profile_fact = Table(  # Marken, Produktgruppen, Leistungen, Vertriebswege, Zielgruppen …
    "company_profile_fact",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("fact_type", String(30), nullable=False),
    Column("value", Text, nullable=False),
    Column("normalized", Text, nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("confidence", String(10), nullable=False),
    Column("method", String(40), nullable=False),
    Column("research_run_id", Integer),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("company_id", "fact_type", "normalized", "source_document_id"),
)

company_market = Table(  # Länder/Regionen: belieferte Märkte und bloße Erwähnungen
    "company_market",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("country_code", String(2)),
    Column("region_label", Text, nullable=False, default=""),
    Column("presence_type", String(20), nullable=False),  # supplied | export | mentioned
    Column("basis", Text, nullable=False),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("confidence", String(10), nullable=False),
    Column("research_run_id", Integer),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "company_id", "country_code", "region_label", "presence_type", "source_document_id"
    ),
)

corporate_event = Table(  # Übernahmen, Verkäufe, Investitionen, Umstrukturierungen …
    "corporate_event",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("event_date", Date),  # wann es geschah/angekündigt wurde (laut Quelle)
    Column("date_precision", String(10), nullable=False, default="unknown"),
    Column("summary", Text, nullable=False),
    Column("counterparty_name", Text),
    Column("counterparty_company_id", Integer, ForeignKey("company.id")),
    Column("affected_headcount", Integer),
    Column("amount", _decimal()),
    Column("currency", String(3)),
    Column("country_code", String(2)),
    Column("source_document_id", Integer, ForeignKey("source_document.id"), nullable=False),
    Column("evidence_id", Integer, ForeignKey("evidence.id"), nullable=False),
    Column("provenance_class", String(30), nullable=False),
    Column("verification_status", String(20), nullable=False),
    Column("confidence", String(10), nullable=False),
    Column("method", String(40), nullable=False),
    Column("research_run_id", Integer),
    Column("captured_at", DateTime(timezone=True), nullable=False),  # wann das System es erfuhr
    Column("dedup_key", String(64), nullable=False, unique=True),
)

profile_change = Table(  # Änderungen außerhalb von Kennzahlen (Standorte, Märkte, Ereignisse …)
    "profile_change",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("subject", String(30), nullable=False),
    Column("change_kind", String(40), nullable=False),
    Column("ref_table", String(40), nullable=False),
    Column("ref_id", Integer, nullable=False),
    Column("summary", Text, nullable=False),
    Column("event_date", Date),
    Column("source_document_id", Integer, ForeignKey("source_document.id")),
    Column("evidence_id", Integer, ForeignKey("evidence.id")),
    Column("research_run_id", Integer),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("ref_table", "ref_id", "change_kind"),
)

company_note = Table(  # interne Notizen; Änderungen erzeugen neue Zeilen
    "company_note",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("section", String(30), nullable=False),
    Column("body", Text, nullable=False),
    Column("author", Text, nullable=False),
    Column("supersedes_id", Integer),
    Column("deleted", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

competitor_assessment = Table(
    "competitor_assessment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("competitor_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("classification", String(30), nullable=False),
    Column("dimensions", Text, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("method_version", String(30), nullable=False),
    Column("research_run_id", Integer),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
)

# ------------------------------------------------------------- Phase 4: Opportunity Intelligence

product_group = Table(  # Yellotools-Produktmatrix (Kopie der gepflegten Datei)
    "product_group",
    metadata,
    Column("key", String(60), primary_key=True),
    Column("name", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("definition", Text, nullable=False),
    Column("matrix_version", String(40), nullable=False, default=""),
    Column("source_path", Text, nullable=False, default=""),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

opportunity_score = Table(  # Bewertung v2: Produktrelevanz, Potenzial, Datenqualität getrennt
    "opportunity_score",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    Column("use_case_key", String(60), nullable=False),
    Column("model_version", String(40), nullable=False),
    Column("product_relevance", Integer),
    Column("business_potential", Integer),
    Column("data_quality", Integer, nullable=False),
    Column("priority_score", Integer),
    Column("segment", String(60), nullable=False),
    Column("details", Text, nullable=False),
    Column("research_run_id", Integer),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
)

watchlist = Table(
    "watchlist",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("description", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

watchlist_entry = Table(  # jede Statusänderung ist eine neue Zeile (Historie)
    "watchlist_entry",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("watchlist_id", Integer, ForeignKey("watchlist.id"), nullable=False),
    Column("company_id", Integer, ForeignKey("company.id"), nullable=False),
    # vorgemerkt | in_pruefung | qualifiziert | verworfen | entfernt
    Column("status", String(20), nullable=False),
    Column("reason", Text, nullable=False, default=""),
    Column("author", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

schema_info = Table(
    "schema_info",
    metadata,
    Column("version", Integer, nullable=False),
)

_IMMUTABLE_OBS_COLUMNS = (
    "company_id, metric, value, unit, currency, period_type, period_start, period_end, "
    "consolidation_scope, provenance_class, source_document_id, evidence_id, methodology, "
    "derived_from, captured_at, content_hash, segment"
)


def _sqlite_triggers() -> list[str]:
    statements = []
    for name in ("financial_observation", "employee_observation"):
        statements.append(f"DROP TRIGGER IF EXISTS {name}_immutable")  # Spaltenliste aktualisieren
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS {name}_immutable BEFORE UPDATE OF "
            f"{_IMMUTABLE_OBS_COLUMNS} ON {name} BEGIN "
            "SELECT RAISE(ABORT, 'Beobachtungen sind unveränderlich (append-only)'); END"
        )
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS {name}_no_delete BEFORE DELETE ON {name} BEGIN "
            "SELECT RAISE(ABORT, 'Beobachtungen werden nie gelöscht'); END"
        )
    statements.append(
        "CREATE TRIGGER IF NOT EXISTS evidence_immutable BEFORE UPDATE ON evidence BEGIN "
        "SELECT RAISE(ABORT, 'Belege sind unveränderlich'); END"
    )
    return statements


def _postgres_triggers() -> list[str]:
    statements = [
        "CREATE OR REPLACE FUNCTION mi_reject_change() RETURNS trigger AS $$ BEGIN "
        "RAISE EXCEPTION 'Beobachtungen sind unveränderlich (append-only)'; END; "
        "$$ LANGUAGE plpgsql"
    ]
    for name in ("financial_observation", "employee_observation"):
        statements.append(f"DROP TRIGGER IF EXISTS {name}_immutable ON {name}")
        statements.append(
            f"CREATE TRIGGER {name}_immutable BEFORE UPDATE OF {_IMMUTABLE_OBS_COLUMNS} "
            f"OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION mi_reject_change()"
        )
    return statements


def make_engine(url: str) -> Engine:
    """Erzeugt die Engine; SQLite mit WAL und Fremdschlüsseln."""
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 10000")
            cursor.close()

    return engine


_ADDED_COLUMNS = {  # Spalten, die nach Version 1 hinzukamen (für bestehende Datenbanken)
    "research_task": {"step": "INTEGER NOT NULL DEFAULT 0", "label": "TEXT NOT NULL DEFAULT ''"},
    # Version 3
    "financial_observation": {"segment": "VARCHAR(80) NOT NULL DEFAULT ''"},
    "employee_observation": {"segment": "VARCHAR(80) NOT NULL DEFAULT ''"},
    "company_location": {
        "latitude": "VARCHAR(48)",
        "longitude": "VARCHAR(48)",
        "geo_precision": "VARCHAR(20) NOT NULL DEFAULT 'none'",
        "geo_note": "TEXT NOT NULL DEFAULT ''",
        "site_status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
        "evidence_id": "INTEGER",
        "research_run_id": "INTEGER",
    },
}


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, columns in _ADDED_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table_name)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def init_db(engine: Engine) -> None:
    """Legt fehlende Tabellen, Spalten und Trigger an (idempotent)."""
    metadata.create_all(engine)
    _add_missing_columns(engine)
    triggers = _sqlite_triggers() if engine.dialect.name == "sqlite" else _postgres_triggers()
    with engine.begin() as conn:
        for statement in triggers:
            conn.execute(text(statement))
        if conn.execute(schema_info.select()).first() is None:
            conn.execute(schema_info.insert().values(version=SCHEMA_VERSION))
        else:
            conn.execute(schema_info.update().values(version=SCHEMA_VERSION))
