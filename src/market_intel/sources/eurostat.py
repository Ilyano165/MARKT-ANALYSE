"""Eurostat Dissemination API (JSON-stat 2.0): Branchenstatistik als Marktkontext.

Endpunkt: ``/eurostat/api/dissemination/statistics/1.0/data/{datensatz}?format=JSON&lang=de``
mit Dimensionsfiltern (z. B. ``geo=DE``, ``nace_r2=C2573``). Bezeichnungen der Indikatoren
werden aus der Antwort übernommen, nicht im Code festgelegt.

Stand: Parser gegen das JSON-stat-2.0-Format getestet; der Standard-Datensatz
(``DEFAULT_DATASET``) ist live **ungeprüft** und in der Konfiguration änderbar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Any

from sqlalchemy import insert, select

from .. import db
from ..context import Ctx

SOURCE = "eurostat"
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
DEFAULT_DATASET = "sbs_ovw_act"


@dataclass(frozen=True, slots=True)
class Cell:
    """Ein Wert der Tabelle mit Klartext-Dimensionen."""

    dimensions: dict[str, str]
    period: str
    value: Decimal
    status: str | None


def parse_jsonstat(data: dict[str, Any]) -> list[Cell]:
    """JSON-stat 2.0 (``id``, ``size``, ``dimension``, ``value``, ``status``) entpacken."""
    ids = data.get("id")
    sizes = data.get("size")
    dims = data.get("dimension")
    values = data.get("value")
    if (
        not isinstance(ids, list)
        or not isinstance(sizes, list)
        or not isinstance(dims, dict)
        or len(ids) != len(sizes)
        or values is None
    ):
        raise ValueError("kein gültiges JSON-stat-2.0-Dokument")
    codes: list[list[str]] = []
    labels: list[dict[str, str]] = []
    for dim_id, size in zip(ids, sizes, strict=True):
        category = (dims.get(dim_id) or {}).get("category") or {}
        index = category.get("index") or {}
        ordered = sorted(index, key=lambda k: index[k]) if isinstance(index, dict) else list(index)
        if len(ordered) != size:
            raise ValueError(f"Dimension {dim_id}: Größe passt nicht zum Index")
        codes.append(ordered)
        labels.append(category.get("label") or {})
    statuses = data.get("status") or {}
    cells = []
    for flat, combo in enumerate(product(*codes)):
        raw = (
            values.get(str(flat))
            if isinstance(values, dict)
            else (values[flat] if flat < len(values) else None)
        )
        if raw is None:
            continue
        named = {
            ids[i]: f"{combo[i]} {labels[i].get(combo[i], '')}".strip()
            for i in range(len(ids))
            if ids[i] != "time"
        }
        period = combo[ids.index("time")] if "time" in ids else ""
        status = statuses.get(str(flat)) if isinstance(statuses, dict) else None
        cells.append(Cell(named, period, Decimal(str(raw)), status))
    return cells


def fetch(
    ctx: Ctx, nace_code: str, country: str, dataset: str = DEFAULT_DATASET
) -> tuple[int, str]:
    """Statistik abrufen und speichern. Gibt (Anzahl Werte, Hinweis) zurück."""
    nace_param = "C" + nace_code.replace(".", "") if nace_code[:1].isdigit() else nace_code
    result = ctx.gateway.get(
        SOURCE,
        f"{BASE}/{dataset}",
        params={"format": "JSON", "lang": "de", "geo": country, "nace_r2": nace_param},
        purpose=f"Eurostat {dataset} {country} {nace_param}",
        title=f"Eurostat {dataset} {country} {nace_param}",
    )
    if not result.ok or result.document_id is None:
        return 0, f"{result.status.value} {result.error}".strip()
    try:
        cells = parse_jsonstat(json.loads(result.content))
    except ValueError as exc:
        return 0, f"Antwort nicht auswertbar: {exc}"
    stored = 0
    with ctx.engine.begin() as conn:
        for cell in cells:
            key = json.dumps(cell.dimensions, ensure_ascii=False, sort_keys=True)
            exists = conn.execute(
                select(db.market_statistic.c.id).where(
                    db.market_statistic.c.source_document_id == result.document_id,
                    db.market_statistic.c.dimensions == key,
                    db.market_statistic.c.period == cell.period,
                )
            ).first()
            if exists:
                continue
            indicator = next((v for k, v in cell.dimensions.items() if k.startswith("indic")), "")
            conn.execute(
                insert(db.market_statistic).values(
                    source_key=SOURCE,
                    dataset=dataset,
                    indicator=indicator,
                    dimensions=key,
                    period=cell.period,
                    value=cell.value,
                    unit=cell.dimensions.get("unit", ""),
                    status_flag=cell.status,
                    source_document_id=result.document_id,
                    captured_at=db.utcnow(),
                )
            )
            stored += 1
    return stored, ""
