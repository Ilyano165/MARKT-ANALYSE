"""Anwendungsfälle (Hypothesen) laden und in der Datenbank registrieren."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from sqlalchemy import Engine, insert, select, update

from . import db


@dataclass(frozen=True, slots=True)
class UseCase:
    """Anwendungsfall mit Suchbegriffen je Quelle."""

    key: str
    name: str
    evidence_status: str
    product_categories: tuple[str, ...]
    website_terms: tuple[str, ...]
    name_terms: tuple[str, ...]
    tender_terms: tuple[str, ...]
    news_terms: tuple[str, ...]
    industry_terms: tuple[str, ...]


def load_use_cases(path: Path | None = None) -> dict[str, UseCase]:
    """Aus Datei laden; ohne Pfad die mitgelieferten Hypothesen."""
    if path is None:
        raw = resources.files("market_intel.data").joinpath("use_cases.toml").read_bytes()
    else:
        raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    cases = {}
    for item in data.get("use_case", []):
        case = UseCase(
            key=str(item["key"]),
            name=str(item["name"]),
            evidence_status=str(item.get("evidence_status", "hypothesis")),
            **{
                f: tuple(str(t) for t in item.get(f, []))
                for f in (
                    "product_categories",
                    "website_terms",
                    "name_terms",
                    "tender_terms",
                    "news_terms",
                    "industry_terms",
                )
            },
        )
        cases[case.key] = case
    return cases


def register(engine: Engine, cases: dict[str, UseCase]) -> None:
    """Anwendungsfälle in die Datenbank schreiben (für Fremdschlüssel und Export)."""
    with engine.begin() as conn:
        for case in cases.values():
            values = {
                "name": case.name,
                "product_categories": json.dumps(case.product_categories, ensure_ascii=False),
                "evidence_status": case.evidence_status,
                "definition": json.dumps(
                    {
                        "website_terms": case.website_terms,
                        "name_terms": case.name_terms,
                        "tender_terms": case.tender_terms,
                        "news_terms": case.news_terms,
                        "industry_terms": case.industry_terms,
                    },
                    ensure_ascii=False,
                ),
            }
            exists = conn.execute(
                select(db.product_use_case.c.key).where(db.product_use_case.c.key == case.key)
            ).first()
            if exists:
                conn.execute(
                    update(db.product_use_case)
                    .where(db.product_use_case.c.key == case.key)
                    .values(**values)
                )
            else:
                conn.execute(insert(db.product_use_case).values(key=case.key, **values))
