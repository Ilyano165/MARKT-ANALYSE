"""EZB-Referenzkurse (Jahresdurchschnitt) für abgeleitete EUR-Werte."""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from ..context import Ctx

SOURCE = "ecb_fx"
URL = "https://data-api.ecb.europa.eu/service/data/EXR/A.{cur}.EUR.SP00.A"


def annual_rates(ctx: Ctx, currency: str) -> tuple[dict[int, Decimal], int | None]:
    """Jahresdurchschnitt: Einheiten ``currency`` je 1 EUR. Gibt (Jahr→Kurs, Dokument) zurück."""
    if not re.fullmatch(r"[A-Z]{3}", currency) or currency == "EUR":
        return {}, None
    result = ctx.gateway.get(
        SOURCE,
        URL.format(cur=currency),
        params={"format": "csvdata"},
        purpose=f"EZB-Jahreskurs {currency}",
        title=f"EZB EXR {currency}",
    )
    if not result.ok:
        ctx.note(f"EZB-Kurse {currency} nicht abrufbar: {result.status.value} {result.error}")
        return {}, None
    rates: dict[int, Decimal] = {}
    for row in csv.DictReader(io.StringIO(result.text)):
        period, value = row.get("TIME_PERIOD", ""), row.get("OBS_VALUE", "")
        if re.fullmatch(r"\d{4}", period) and value:
            rates[int(period)] = Decimal(value)
    return rates, result.document_id
