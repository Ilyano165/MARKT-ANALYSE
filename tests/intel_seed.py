"""Beispieldaten für die Unternehmensanalyse (erfundene Testfirmen, keine echten Zahlen).

Alles läuft über die manuelle Erfassung mit Beleg – damit ist auch dieser Weg getestet.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Engine

from market_intel import repo
from market_intel.intel import manual
from market_intel.sources.user_data import store_document

REPORT = """Geschäftsbericht Muster Folien GmbH (Testdokument)
Umsatzerlöse Konzern 2018: 40.000.000 EUR.
Umsatzerlöse Konzern 2019: 44.000.000 EUR.
Umsatzerlöse Konzern 2021: 50.600.000 EUR.
Umsatzerlöse Konzern 2022: 66.000.000 EUR.
Umsatzerlöse Konzern 2023: 62.700.000 EUR.
Am 1. Juli 2022 übernahm die Muster Folien GmbH die Beta Tape AG.
Mitarbeiter zum 31.12.2019: 310 Personen.
Mitarbeiter zum 31.12.2020: 320 Personen.
Mitarbeiter zum 31.12.2022: 410 Personen.
Mitarbeiter zum 31.12.2023: 400 Personen.
Davon in Deutschland zum 31.12.2023: 350 Personen.
Im März 2023 wurden 30 Stellen in der Verwaltung abgebaut.
Unser Werk in Leipzig fertigt Car-Wrapping-Folien.
Wir exportieren nach Frankreich und Österreich.
Produktgruppen: Car-Wrapping-Folien, Schutzfolien.
Zielgruppe sind Fahrzeugfolierer.
"""

RIVAL = """Unternehmensprofil Rivale Wrap AG (Testdokument)
Umsatzerlöse 2023: 31.000.000 EUR.
Produktgruppen: Car-Wrapping-Folien, Schutzfolien, Werkzeuge.
Zielgruppe sind Fahrzeugfolierer. Wir liefern nach Frankreich.
"""

OTHER = """Profil Andere Branche GmbH (Testdokument)
Produktgruppen: Industriekleber.
"""


def _doc(engine: Engine, name: str, text: str) -> int:
    return store_document(
        engine,
        "user_upload",
        text.encode("utf-8"),
        url=f"upload:{name}",
        title=name,
        publisher="Testdaten",
        published=None,
        content_type="text/plain",
    )


def seed(engine: Engine) -> dict[str, int]:
    """Legt drei Firmen mit belegten Angaben an; gibt deren IDs zurück."""
    repo.sync_sources(engine, (), (), False)
    with engine.begin() as conn:
        muster, _ = repo.resolve_or_create(
            conn, "Muster Folien GmbH", [("de_register", "Leipzig HRB 1")], document_id=None
        )
        rival, _ = repo.resolve_or_create(
            conn, "Rivale Wrap AG", [("de_register", "Köln HRB 2")], document_id=None
        )
        other, _ = repo.resolve_or_create(
            conn, "Andere Branche GmbH", [("de_register", "Bonn HRB 3")], document_id=None
        )
        repo.resolve_or_create(
            conn, "Beta Tape AG", [("de_register", "Kiel HRB 4")], document_id=None
        )
    report = _doc(engine, "gb-muster.txt", REPORT)
    rival_doc = _doc(engine, "profil-rivale.txt", RIVAL)
    other_doc = _doc(engine, "profil-andere.txt", OTHER)
    for year, value in (
        (2018, 40_000_000),
        (2019, 44_000_000),
        (2021, 50_600_000),
        (2022, 66_000_000),
        (2023, 62_700_000),
    ):
        manual.add_figure(
            engine,
            company_id=muster,
            document_id=report,
            quote=f"Umsatzerlöse Konzern {year}: {value:,} EUR.".replace(",", "."),
            locator="S. 3",
            metric="revenue",
            value=Decimal(value),
            currency="EUR",
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            scope="group",
            provenance="official",
        )
    for year, value in ((2019, 310), (2020, 320), (2022, 410), (2023, 400)):
        manual.add_figure(
            engine,
            company_id=muster,
            document_id=report,
            quote=f"Mitarbeiter zum 31.12.{year}: {value} Personen.",
            locator="S. 5",
            metric="headcount_total",
            value=Decimal(value),
            period_end=date(year, 12, 31),
            scope="group",
            provenance="official",
        )
    manual.add_figure(
        engine,
        company_id=muster,
        document_id=report,
        quote="Davon in Deutschland zum 31.12.2023: 350 Personen.",
        locator="S. 5",
        metric="headcount_total",
        value=Decimal(350),
        period_end=date(2023, 12, 31),
        scope="group",
        provenance="official",
        segment="country:DE",
    )
    manual.add_event(
        engine,
        company_id=muster,
        document_id=report,
        quote="Am 1. Juli 2022 übernahm die Muster Folien GmbH die Beta Tape AG.",
        locator="S. 2",
        event_type="acquisition",
        event_date="2022-07-01",
        summary="Übernahme der Beta Tape AG",
        provenance="official",
        counterparty="Beta Tape AG",
    )
    manual.add_event(
        engine,
        company_id=muster,
        document_id=report,
        quote="Im März 2023 wurden 30 Stellen in der Verwaltung abgebaut.",
        locator="S. 6",
        event_type="layoff",
        event_date="2023-03",
        summary="Abbau von 30 Stellen in der Verwaltung",
        provenance="official",
        affected_headcount=30,
    )
    manual.add_location(
        engine,
        company_id=muster,
        document_id=report,
        quote="Unser Werk in Leipzig fertigt Car-Wrapping-Folien.",
        locator="S. 4",
        location_type="production",
        country="DE",
        city="Leipzig",
        latitude=Decimal("51.34"),
        longitude=Decimal("12.37"),
        geo_precision="city",
        geo_note="Ortskoordinaten Leipzig (Testwert)",
    )
    for country in ("FR", "AT"):
        manual.add_market(
            engine,
            company_id=muster,
            document_id=report,
            quote="Wir exportieren nach Frankreich und Österreich.",
            locator="S. 4",
            country=country,
            region="",
            presence="export",
        )
    for company, doc, groups in (
        (muster, report, ("Car-Wrapping-Folien", "Schutzfolien")),
        (rival, rival_doc, ("Car-Wrapping-Folien", "Schutzfolien", "Werkzeuge")),
        (other, other_doc, ("Industriekleber",)),
    ):
        for group in groups:
            manual.add_fact(
                engine,
                company_id=company,
                document_id=doc,
                quote=group,
                locator="Produktgruppen",
                fact_type="product_group",
                value=group,
            )
    for company, doc in ((muster, report), (rival, rival_doc)):
        manual.add_fact(
            engine,
            company_id=company,
            document_id=doc,
            quote="Zielgruppe sind Fahrzeugfolierer.",
            locator="Zielgruppe",
            fact_type="target_group",
            value="Fahrzeugfolierer",
        )
    manual.add_market(
        engine,
        company_id=rival,
        document_id=rival_doc,
        quote="Wir liefern nach Frankreich.",
        locator="Profil",
        country="FR",
        region="",
        presence="supplied",
    )
    manual.add_figure(
        engine,
        company_id=rival,
        document_id=rival_doc,
        quote="Umsatzerlöse 2023: 31.000.000 EUR.",
        locator="Profil",
        metric="revenue",
        value=Decimal(31_000_000),
        currency="EUR",
        period_end=date(2023, 12, 31),
        scope="unknown",
        provenance="company_reported",
    )
    return {"muster": muster, "rival": rival, "other": other, "report": report}
