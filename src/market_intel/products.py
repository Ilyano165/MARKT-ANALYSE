"""Yellotools-Produktmatrix: laden, prüfen, pflegen (CSV/TOML) und in der Datenbank ablegen.

Die Matrix ist der Ausgangspunkt der Kundensuche: Produktgruppe → Materialien, Prozesse,
Anwender, Anwendungsfälle, Branchen, Grenzen, Quellen. Alternative Anwender sind Hypothesen.
"""

from __future__ import annotations

import csv
import io
import json
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, insert, select, update

from . import db
from .usecases import UseCase

LIST_FIELDS = (
    "examples",
    "materials",
    "processes",
    "typical_users",
    "alternative_users",
    "applications",
    "industries",
    "limitations",
    "process_terms",
    "sources",
)
REQUIRED = ("key", "name", "description", "materials", "processes", "typical_users", "sources")
STATUSES = ("researched", "confirmed", "draft", "discontinued")


@dataclass(frozen=True, slots=True)
class ProductGroup:
    """Produktgruppe mit Funktionen, Grenzen und Quellen."""

    key: str
    name: str
    status: str
    description: str
    examples: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    typical_users: tuple[str, ...] = ()
    alternative_users: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    process_terms: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    confirmed_by: str = ""
    confirmed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "description": self.description,
            **{f: list(getattr(self, f)) for f in LIST_FIELDS},
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
        }


@dataclass(slots=True)
class Catalog:
    """Gesamte Matrix inkl. Metadaten und Werkzeug-Erwähnungen."""

    meta: dict[str, Any]
    products: dict[str, ProductGroup]
    own_brand_terms: tuple[str, ...] = ()
    other_tool_terms: tuple[str, ...] = ()
    source_path: str = "mitgeliefert"
    problems: list[str] = field(default_factory=list)


def _group(item: dict[str, Any]) -> ProductGroup:
    return ProductGroup(
        key=str(item.get("key", "")).strip(),
        name=str(item.get("name", "")).strip(),
        status=str(item.get("status", "draft")),
        description=str(item.get("description", "")).strip(),
        confirmed_by=str(item.get("confirmed_by", "")),
        confirmed_at=str(item.get("confirmed_at", "")),
        **{
            f: tuple(str(v).strip() for v in item.get(f, []) if str(v).strip()) for f in LIST_FIELDS
        },
    )


def parse_catalog(text: str, source_path: str = "mitgeliefert") -> Catalog:
    """TOML-Text → Matrix (ohne Querverweis-Prüfung)."""
    data = tomllib.loads(text)
    mentions = data.get("tool_mentions", {})
    products: dict[str, ProductGroup] = {}
    problems: list[str] = []
    for item in data.get("product", []):
        group = _group(item)
        if group.key in products:
            problems.append(f"Produktgruppe {group.key} doppelt")
        products[group.key] = group
    return Catalog(
        meta=dict(data.get("meta", {})),
        products=products,
        own_brand_terms=tuple(str(t) for t in mentions.get("own_brand", [])),
        other_tool_terms=tuple(str(t) for t in mentions.get("other_brands", [])),
        source_path=source_path,
        problems=problems,
    )


def load_products(path: Path | None = None) -> Catalog:
    """Eigene Datei oder mitgelieferte Matrix."""
    if path is None:
        text = resources.files("market_intel.data").joinpath("products.toml").read_text("utf-8")
        return parse_catalog(text)
    return parse_catalog(path.read_text("utf-8"), str(path))


def validate(catalog: Catalog, cases: dict[str, UseCase]) -> list[str]:
    """Vollständigkeit und Querverweise prüfen. Leere Liste = in Ordnung."""
    problems = list(catalog.problems)
    for group in catalog.products.values():
        for name in REQUIRED:
            value = getattr(group, name)
            if not value:
                problems.append(f"{group.key or '?'}: Pflichtfeld {name} fehlt")
        if group.status not in STATUSES:
            problems.append(
                f"{group.key}: Status {group.status!r} unbekannt ({', '.join(STATUSES)})"
            )
        if group.status == "confirmed" and not (group.confirmed_by and group.confirmed_at):
            problems.append(f"{group.key}: bestätigt, aber ohne confirmed_by/confirmed_at")
        for source in group.sources:
            if not source.split("|", 1)[0].strip().startswith(("https://", "http://", "intern:")):
                problems.append(f"{group.key}: Quelle ohne URL oder 'intern:'-Verweis: {source}")
        for app in group.applications:
            if app not in cases:
                problems.append(f"{group.key}: Anwendungsfall {app} existiert nicht")
        if not group.process_terms:
            problems.append(f"{group.key}: keine process_terms – Prozessbelege nicht erkennbar")
    for case in cases.values():
        for key in case.product_groups:
            if key not in catalog.products:
                problems.append(
                    f"Anwendungsfall {case.key}: Produktgruppe {key} fehlt in der Matrix"
                )
        if not case.product_groups:
            problems.append(f"Anwendungsfall {case.key}: keine Produktgruppe zugeordnet")
        if not case.website_terms:
            problems.append(f"Anwendungsfall {case.key}: keine website_terms (kein Beleg möglich)")
    return problems


def products_for(catalog: Catalog, case: UseCase) -> list[ProductGroup]:
    """Produktgruppen eines Anwendungsfalls (in Reihenfolge der Matrix)."""
    return [catalog.products[k] for k in case.product_groups if k in catalog.products]


def register_products(engine: Engine, catalog: Catalog) -> None:
    """Matrix in der Datenbank ablegen (für Export, Oberfläche, Nachvollziehbarkeit)."""
    version = str(catalog.meta.get("version", ""))
    with engine.begin() as conn:
        for group in catalog.products.values():
            values = {
                "name": group.name,
                "status": group.status,
                "definition": json.dumps(group.as_dict(), ensure_ascii=False),
                "matrix_version": version,
                "source_path": catalog.source_path,
                "updated_at": db.utcnow(),
            }
            exists = conn.execute(
                select(db.product_group.c.key).where(db.product_group.c.key == group.key)
            ).first()
            if exists:
                conn.execute(
                    update(db.product_group)
                    .where(db.product_group.c.key == group.key)
                    .values(**values)
                )
            else:
                conn.execute(insert(db.product_group).values(key=group.key, **values))


# ---------------------------------------------------------------------- Pflege (CSV ↔ TOML)

CSV_COLUMNS = ("key", "name", "status", "description", *LIST_FIELDS, "confirmed_by", "confirmed_at")
LIST_SEPARATOR = ";"


def export_csv(catalog: Catalog) -> str:
    """Matrix als CSV (Listen mit „;“ getrennt) – Vorlage zum Bearbeiten in Excel/LibreOffice."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=",", quoting=csv.QUOTE_ALL)
    writer.writerow(CSV_COLUMNS)
    for group in catalog.products.values():
        data = group.as_dict()
        writer.writerow(
            [
                f"{LIST_SEPARATOR} ".join(data[c]) if c in LIST_FIELDS else data[c]
                for c in CSV_COLUMNS
            ]
        )
    return out.getvalue()


def import_csv(text: str, base: Catalog) -> tuple[Catalog, list[str]]:
    """CSV einlesen; Zeilen ersetzen gleichnamige Produktgruppen der Basis, neue kommen hinzu."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in ("key", "name") if c not in (reader.fieldnames or [])]
    if missing:
        return base, [f"Spalte fehlt: {', '.join(missing)}"]
    products = dict(base.products)
    notes = []
    for number, row in enumerate(reader, start=2):
        if not (row.get("key") or "").strip():
            notes.append(f"Zeile {number}: ohne key übersprungen")
            continue
        item: dict[str, Any] = {k: (v or "").strip() for k, v in row.items() if k}
        for name in LIST_FIELDS:
            item[name] = [x.strip() for x in item.get(name, "").split(LIST_SEPARATOR) if x.strip()]
        group = _group(item)
        notes.append(f"Zeile {number}: {group.key} {'ersetzt' if group.key in products else 'neu'}")
        products[group.key] = group
    return (
        Catalog(base.meta, products, base.own_brand_terms, base.other_tool_terms, "csv-import"),
        notes,
    )


def to_toml(catalog: Catalog) -> str:
    """Matrix als TOML (für eine eigene products_file)."""

    def value(v: Any) -> str:
        if isinstance(v, list | tuple):
            return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
        return json.dumps(str(v), ensure_ascii=False)

    lines = ["# Yellotools-Produktmatrix (gepflegt mit mi products)", "", "[meta]"]
    lines += [f"{k} = {value(v)}" for k, v in catalog.meta.items()]
    lines += [
        "",
        "[tool_mentions]",
        f"own_brand = {value(catalog.own_brand_terms)}",
        f"other_brands = {value(catalog.other_tool_terms)}",
    ]
    for group in catalog.products.values():
        lines += ["", "[[product]]"]
        lines += [f"{k} = {value(v)}" for k, v in group.as_dict().items() if v not in ("", [])]
    return "\n".join(lines) + "\n"
