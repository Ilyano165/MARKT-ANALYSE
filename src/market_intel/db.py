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
    text,
)
from sqlalchemy.types import TypeDecorator

SCHEMA_VERSION = 1


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

schema_info = Table(
    "schema_info",
    metadata,
    Column("version", Integer, nullable=False),
)

_IMMUTABLE_OBS_COLUMNS = (
    "company_id, metric, value, unit, currency, period_type, period_start, period_end, "
    "consolidation_scope, provenance_class, source_document_id, evidence_id, methodology, "
    "derived_from, captured_at, content_hash"
)


def _sqlite_triggers() -> list[str]:
    statements = []
    for name in ("financial_observation", "employee_observation"):
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


def init_db(engine: Engine) -> None:
    """Legt fehlende Tabellen und Trigger an (idempotent)."""
    metadata.create_all(engine)
    triggers = _sqlite_triggers() if engine.dialect.name == "sqlite" else _postgres_triggers()
    with engine.begin() as conn:
        for statement in triggers:
            conn.execute(text(statement))
        if conn.execute(schema_info.select()).first() is None:
            conn.execute(schema_info.insert().values(version=SCHEMA_VERSION))
