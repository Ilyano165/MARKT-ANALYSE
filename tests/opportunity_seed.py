"""Erfundene Testfirmen aus verschiedenen Branchen (Webseiten als gespeicherte Dokumente).

Die Seiten durchlaufen die echte Extraktion (Schritt 4); die Bewertung nutzt nur, was dabei
mit Zitat gespeichert wurde.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, insert

from market_intel import db, repo
from market_intel.engine.extract import extract_company_documents
from market_intel.intel import manual
from market_intel.sources.user_data import store_document

SITES: dict[str, dict[str, Any]] = {
    "folierer": {
        "name": "Folienprofi Fahrzeugdesign GmbH",
        "country": "DE",
        "url": "https://folienprofi.test/leistungen",
        "html": "<html><head><title>Leistungen</title></head><body>"
        "<h1>Fahrzeugfolierung in 12 Filialen</h1>"
        "<p>Wir bieten Vollfolierung, Teilfolierung und Flottenbeschriftung für Gewerbekunden.</p>"
        "<p>Zusätzlich montieren wir Lackschutzfolie (PPF) auf Neufahrzeugen.</p>"
        "</body></html>",
    },
    "messebau": {
        "name": "Messewerk Ladenbau GmbH",
        "country": "AT",
        "url": "https://messewerk.test/",
        "html": "<html><head><title>Messewerk</title></head><body>"
        "<p>Messebau und Ladenbau aus einer Hand: Messestand, Shopdesign und Bodengrafik.</p>"
        "<p>Wir produzieren Großformatgrafik und montieren Wandfolie direkt vor Ort.</p>"
        "</body></html>",
    },
    "remarketing": {
        "name": "Flotten Remarketing AG",
        "country": "DE",
        "url": "https://flotten-remarketing.test/service",
        "html": "<html><head><title>Service</title></head><body>"
        "<p>Aufbereitung von Leasingrückläufern: Entfolierung, Beschriftung entfernen und "
        "Kleberückstände beseitigen vor dem Remarketing.</p></body></html>",
    },
    "glas": {
        "name": "Glasklar Sonnenschutz GmbH",
        "country": "CH",
        "url": "https://glasklar.test/",
        "html": "<html><head><title>Glasklar</title></head><body>"
        "<p>Montage von Sonnenschutzfolie, Sichtschutzfolie und Sicherheitsfolie in Nassverklebung "
        "für Büros und Geschäfte.</p></body></html>",
    },
    "bank": {
        "name": "Großbank Finanz AG",
        "country": "DE",
        "url": "https://grossbank.test/",
        "html": "<html><head><title>Großbank</title></head><body>"
        "<p>Wir finanzieren Fahrzeugflotten, Immobilien und Maschinen für den Mittelstand.</p>"
        "<p>Rund 25.000 Mitarbeiter betreuen unsere Kunden in 40 Ländern.</p></body></html>",
    },
    "folien_ohne_daten": {
        "name": "Kleine Folierwerkstatt",
        "country": None,
        "url": "https://folierwerkstatt.test/",
        "html": "<html><head><title>Werkstatt</title></head><body>"
        "<p>Fahrzeugfolierung, Vollfolierung und Teilfolierung – "
        "Flottenbeschriftung auf Anfrage.</p>"
        "</body></html>",
    },
    "folien_klein": {
        "name": "Folierwerkstatt Mini",
        "country": None,
        "url": "https://folierwerkstatt-mini.test/",
        "html": "<html><head><title>Werkstatt</title></head><body>"
        "<p>Fahrzeugfolierung, Vollfolierung und Teilfolierung – "
        "Flottenbeschriftung auf Anfrage.</p>"
        "</body></html>",
    },
}


def _website_document(engine: Engine, url: str, html: str) -> int:
    body = html.encode("utf-8")
    with engine.begin() as conn:
        return int(
            conn.execute(
                insert(db.source_document).values(
                    source_key="website",
                    url=url,
                    final_url=url,
                    request_body_sha256="",
                    title=url,
                    retrieved_at=db.utcnow(),
                    http_status=200,
                    content_type="text/html",
                    sha256=hashlib.sha256(body).hexdigest(),
                    body=body,
                )
            ).inserted_primary_key[0]
        )


def seed_candidates(engine: Engine, ctx: Any, cases: dict[str, Any]) -> dict[str, int]:
    """Legt die Testfirmen an und lässt die Extraktion über ihre Seiten laufen."""
    ids: dict[str, int] = {}
    for key, site in SITES.items():
        with engine.begin() as conn:
            company_id = repo.create_company(
                conn, site["name"], country_code=site["country"], website=site["url"]
            )
            repo.add_alias(conn, company_id, site["name"], "display_name", None)
        document_id = _website_document(engine, site["url"], site["html"])
        extract_company_documents(ctx, company_id, [document_id], cases, [])
        ids[key] = company_id
    # Größe/Entwicklung belegen (Bank groß und wachsend, Folierer mit Wachstum)
    report = store_document(
        engine,
        "user_upload",
        (
            "Jahresabschluss Folienprofi 2022: 40 Mitarbeiter. 2023: 52 Mitarbeiter.\n"
            "Großbank 2022: 24.000 Mitarbeiter. 2023: 25.000 Mitarbeiter.\n"
            "Folierwerkstatt Mini 2023: 3 Mitarbeiter.\n"
            "Im November 2025 eröffnete Folienprofi zwei neue Filialen in Österreich."
        ).encode(),
        url="upload:abschluesse.txt",
        title="Abschlüsse (Testdaten)",
        publisher=None,
        published=None,
        content_type="text/plain",
    )
    from datetime import date

    for key, quote, year, value in (
        ("folierer", "Jahresabschluss Folienprofi 2022: 40 Mitarbeiter.", 2022, 40),
        ("folierer", "2023: 52 Mitarbeiter.", 2023, 52),
        ("bank", "Großbank 2022: 24.000 Mitarbeiter.", 2022, 24_000),
        ("bank", "2023: 25.000 Mitarbeiter.", 2023, 25_000),
        ("folien_klein", "Folierwerkstatt Mini 2023: 3 Mitarbeiter.", 2023, 3),
    ):
        manual.add_figure(
            engine,
            company_id=ids[key],
            document_id=report,
            quote=quote,
            locator="Testdaten",
            metric="headcount_total",
            value=Decimal(value),
            period_end=date(year, 12, 31),
            provenance="official",
        )
    manual.add_event(
        engine,
        company_id=ids["folierer"],
        document_id=report,
        quote="Im November 2025 eröffnete Folienprofi zwei neue Filialen in Österreich.",
        locator="Testdaten",
        event_type="new_site",
        event_date="2025-11",
        summary="zwei neue Filialen in Österreich",
        provenance="official",
    )
    return ids
