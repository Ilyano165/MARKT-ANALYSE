"""Extraktion, Entity Resolution, append-only, Konflikte, Analyse, Scoring, Export, Config, KI."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

from market_intel import db, repo
from market_intel.analytics import growth
from market_intel.config import ConfigError, load_settings
from market_intel.domain import (
    Confidence,
    EvidenceDraft,
    ObservationDraft,
    PeriodType,
    ProvenanceClass,
    Scope,
)
from market_intel.export import safe_cell
from market_intel.extraction.text import (
    find_employee_mentions,
    find_keyword_hits,
    html_to_text,
    number_in_quote,
    parse_number,
    quote_in_text,
)
from market_intel.resolution import legal_form_of, name_similarity, normalize_name
from market_intel.scoring import assess
from market_intel.usecases import load_use_cases, register


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234", "1234"),
        ("1,234", "1234"),
        ("1.234,5", "1234.5"),
        ("1,234.5", "1234.5"),
        ("12,5", "12.5"),
        ("2400", "2400"),
        ("1 200", "1200"),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == Decimal(expected)


def test_employee_mentions_and_quotes():
    text_ = "Über uns\nMit rund 1.200 Mitarbeitern an 5 Standorten.\nFolierung seit 1990."
    mentions = find_employee_mentions(text_)
    assert [m.value for m in mentions] == [Decimal(1200)]
    assert mentions[0].approximate
    assert quote_in_text(mentions[0].quote, text_)
    assert number_in_quote(Decimal(1200), mentions[0].quote)
    assert not quote_in_text("rund 9.999 Mitarbeitern", text_)


def test_html_text_skips_scripts_and_collects_links():
    page = html_to_text(
        '<title>T</title><p>Hallo <a href="/x">Link</a></p><script>geheim()</script>',
        "https://a.test/",
    )
    assert page.title == "T" and "geheim" not in page.text
    assert page.links == (("https://a.test/x", "Link"),)


def test_keyword_hits_are_word_anchored():
    hits = find_keyword_hits("Wir bieten Fahrzeugfolierung an.", ("Folierung", "Fahrzeugfolierung"))
    assert [h.term for h in hits] == ["Fahrzeugfolierung"]


def test_name_normalization():
    assert normalize_name("ORAFOL Europe GmbH") == "orafol europe"
    assert normalize_name("Müller Werbetechnik GmbH & Co. KG") == "mueller werbetechnik"
    assert normalize_name("3M Company") == "3m"
    assert legal_form_of("Beta Werbetechnik GmbH") == "gmbh"
    assert name_similarity("Avery Dennison", "Avery Dennison Corporation") == 1.0
    assert name_similarity("Alpha GmbH", "Beta AG") < 0.6


def _company(engine) -> int:
    with engine.begin() as conn:
        return repo.create_company(conn, "Test GmbH", country_code="DE")


def _document(engine, body: bytes = b"Umsatz 100 Mio") -> int:
    with engine.begin() as conn:
        repo.sync_sources(engine, (), (), False)
        return int(
            conn.execute(
                insert(db.source_document).values(
                    source_key="website",
                    url="https://t.test/",
                    request_body_sha256="",
                    retrieved_at=db.utcnow(),
                    sha256="x" * 64,
                    body=body,
                )
            ).inserted_primary_key[0]
        )


def _draft(
    company_id,
    doc_id,
    value,
    *,
    provenance=ProvenanceClass.COMPANY_REPORTED,
    end=date(2024, 12, 31),
    locator="p1",
    verified=True,
    metric="headcount_total",
):
    return ObservationDraft(
        company_id=company_id,
        kind="employee",
        metric=metric,
        value=Decimal(value),
        unit="persons",
        period_type=PeriodType.POINT_IN_TIME,
        period_start=None,
        period_end=end,
        provenance_class=provenance,
        confidence=Confidence.MEDIUM,
        evidence=EvidenceDraft(
            doc_id, locator, f"{value} Mitarbeiter", "regex_rule", "t", verified
        ),
        scope=Scope.UNKNOWN,
    )


def test_observations_are_append_only(engine):
    company_id, doc_id = _company(engine), _document(engine)
    with engine.begin() as conn:
        obs = repo.ingest_observation(conn, _draft(company_id, doc_id, 100), None)
    with pytest.raises(DBAPIError, match="unveränderlich"), engine.begin() as conn:
        conn.execute(
            text("UPDATE employee_observation SET value = '999' WHERE id = :i"),
            {"i": obs.observation_id},
        )
    with pytest.raises(DBAPIError, match="nie gelöscht"), engine.begin() as conn:
        conn.execute(text("DELETE FROM employee_observation"))
    with engine.begin() as conn:  # Prüfstatus darf sich ändern
        conn.execute(text("UPDATE employee_observation SET verification_status='confirmed'"))


def test_duplicates_and_conflicts(engine):
    company_id, doc_id = _company(engine), _document(engine)
    with engine.begin() as conn:
        first = repo.ingest_observation(conn, _draft(company_id, doc_id, 100), None)
        again = repo.ingest_observation(conn, _draft(company_id, doc_id, 100), None)
        near = repo.ingest_observation(conn, _draft(company_id, doc_id, 104, locator="p2"), None)
        far = repo.ingest_observation(conn, _draft(company_id, doc_id, 150, locator="p3"), None)
    assert not again.created and again.observation_id == first.observation_id
    assert near.conflict_with == ()  # innerhalb Toleranz (±10 Personen)
    assert set(far.conflict_with) == {first.observation_id, near.observation_id}
    with engine.connect() as conn:
        current = repo.current_values(conn, company_id, "employee", "headcount_total")
    assert current[0]["conflict"] and current[0]["alternatives"] == 2


def test_unverified_evidence_is_never_stored(engine):
    company_id, doc_id = _company(engine), _document(engine)
    with pytest.raises(ValueError, match="verifizierten"), engine.begin() as conn:
        repo.ingest_observation(conn, _draft(company_id, doc_id, 100, verified=False), None)


def test_derived_values_need_methodology():
    with pytest.raises(ValueError, match="Methodik"):
        _draft(1, 1, 5, provenance=ProvenanceClass.DERIVED)


def test_official_wins_over_company_reported(engine):
    company_id, doc_id = _company(engine), _document(engine)
    with engine.begin() as conn:
        repo.ingest_observation(conn, _draft(company_id, doc_id, 100, locator="a"), None)
        repo.ingest_observation(
            conn,
            _draft(company_id, doc_id, 101, locator="b", provenance=ProvenanceClass.OFFICIAL),
            None,
        )
        current = repo.current_values(conn, company_id, "employee", "headcount_total")
    assert current[0]["value"] == Decimal(101)
    assert current[0]["provenance_class"] == "official"


def _row(value, end, *, start=None, scope="group", ptype="fiscal_year", prov="official"):
    return {
        "value": Decimal(value),
        "period_end": end,
        "period_start": start,
        "unit": "USD",
        "currency": "USD",
        "consolidation_scope": scope,
        "period_type": ptype,
        "provenance_class": prov,
    }


def test_growth_only_between_comparable_values():
    series = [
        _row(100, date(2021, 12, 31), start=date(2021, 1, 1)),
        _row(110, date(2022, 12, 31), start=date(2022, 1, 1)),
        _row(121, date(2023, 12, 31), start=date(2023, 1, 1)),
    ]
    result = growth(series)
    assert result.cagr_pct == Decimal("10.0")
    assert [v for _, v in result.yoy] == [Decimal("10.0"), Decimal("10.0")]
    mixed_scope = [
        _row(100, date(2022, 12, 31), scope="single_entity"),
        _row(300, date(2023, 12, 31)),
    ]
    assert growth(mixed_scope).cagr_pct is None
    assert growth(series[:1]).cagr_pct is None


def test_scoring_renormalizes_and_flags_missing_data(engine):
    company_id = _company(engine)
    with engine.connect() as conn:
        result = assess(conn, company_id, "fahrzeugfolierung")
    assert result["priority"] == "insufficient_data" and result["completeness"] == 0
    doc_id = _document(engine)
    register(engine, load_use_cases())
    with engine.begin() as conn:
        conn.execute(
            insert(db.company_product_fit).values(
                company_id=company_id,
                use_case_key="fahrzeugfolierung",
                fit_level="strong",
                rationale="Test",
                evidence_ids=json.dumps([]),
                assessed_by="t",
                assessed_at=db.utcnow(),
            )
        )
        repo.ingest_observation(conn, _draft(company_id, doc_id, 45), None)
    with engine.connect() as conn:
        result = assess(conn, company_id, "fahrzeugfolierung")
    assert result["completeness"] == 55 and result["missing"] == ["growth", "signals"]
    assert result["priority"] in ("A", "B")


def test_formula_injection_is_neutralised():
    assert safe_cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert safe_cell("@SUM(A1)").startswith("'")
    assert safe_cell(Decimal("12.5")) == 12.5
    assert safe_cell(None) == ""


def test_config_rejects_secrets(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('contact_email = "a@b.de"\napi_key = "geheim"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)
    path.write_text('contact_email = "a@b.de"\n[llm]\nenabled = true\n', encoding="utf-8")
    assert load_settings(path).llm.enabled
