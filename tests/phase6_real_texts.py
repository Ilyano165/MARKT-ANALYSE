# ruff: noqa: E501  (wörtliche Zitate werden nicht umbrochen)
"""Echte Texte aus dem Live-Test (abgerufen 2026-10-23, gespeichert in live-out/, nicht
eingecheckt). Wörtlich übernommen, je Seite in Originalreihenfolge; nichts ergänzt oder
umformuliert. Kurze Einträge ohne Satzzeichen sind Menüpunkte/Kategorien/Titel der Seite.

Quelle: tests/manual/test_live_integration.py, Protokoll docs/proof/2026-10-23_live-test_phase5.md.
"""

from __future__ import annotations

RETRIEVED = "2026-10-23"

ORAFOL: dict[str, list[str]] = {
    "https://www.orafol.com/en": [
        "Vehicle wrapping",
        "Banners",
    ],
    "https://www.orafol.com/en/about-orafol/career/experts-with-professional-experience": [
        "Our production facilities are at the heart of ORAFOL.",
        "This is where refined film wrapping systems are produced, which our international group of companies sells in over 100 countries.",
    ],
    "https://www.orafol.com/en/about-orafol/career/apprenticeship": [
        "Provided that they consume less and less raw materials and energy and have a really long shelf life, so that hardly any waste is produced - as is the case with ORAFOL.",
    ],
}

AVERY: dict[str, list[str]] = {
    "https://graphics.averydennison.com/en/home.html": [
        "Vehicle Wrapping Films",
        "Paint Protection Films",
        "Discover a dependable, 7-year clear PPF engineered for daily commuters that delivers trusted Avery Dennison protection at a smarter price point.",
        "Locate the best qualified installers to turn your ideas into reality.",
        "Our elite network of specially trained Certified Wrap Installers are ready to provide the best quality products and installation services for your vehicle.",
    ],
    "https://graphics.averydennison.com/en/home/about-us.html": [
        "From architectural design to vehicle wraps and fleets, our graphic solutions promote creativity and deliver the highest performance for your brand.",
    ],
    "https://graphics.averydennison.com/en/home/news/press-releases.html": [
        "Avery Dennison® (NYSE:AVY) Graphics Solutions (NYSE:AVY) today premiered an industry-leading training and certification program for Architectural Window Film installation.",
    ],
    "https://graphics.averydennison.com/en/home/news/newsletter.html": [
        "A new contest, window film show & more!",
    ],
    "https://graphics.averydennison.com/en/home/products.html": [
        "We are passionately focused on continuous innovation resulting in digital vinyl graphics and custom vehicle wrap graphics that are more exciting, impactful and easy to install.",
    ],
}

UMDASCH: dict[str, list[str]] = {
    "https://www.umdasch.com/de/": [
        "Ladenbau",
        "Store Design",
        "Logistik, Rollout und Montage",
        "umdasch The Store Makers - handwerklich verwurzelt, visionär im Digitalen, stets mit menschlichem Anspruch und einem Ziel: Erfolgreiche Stores zu realisieren.",
        "Das Ladenbau-Team der Store Makers übernahm erneut die Umsetzung verschiedenster Shop-Equipment-Lösungen für Kölle Zoo.",
    ],
    "https://www.umdasch.com/de/kontakt": [
        "Unser Team setzt die Betreuung auf Wunsch auch nach der Installation der digitalen Lösungen in Form eines Wartungs- und Service-Pakets fort.",
    ],
    "https://www.umdasch.com/karriere": [
        "An rund 18 Standorten in Europa und der Türkei sorgen die Store Makers von umdasch für außergewöhnliche Projekte im Ladenbau, General Contracting und Digital Retail.",
    ],
    "https://www.umdasch.com/de/news/integrierte-loesungen-fuer-tankstellen-und-convenience-shops-umdasch-the-store-makers-auf-der-uniti-expo-2026": [
        "Am Messestand (Halle 1 / 1C25) wird das Szenario eines Tankstellenshops simuliert – von digitalen Preis- und Content-Updates über Retail-Media-Einbindungen bis hin zur Integration in den analogen Ladenbau.",
        "Das Unternehmen ist dort bereits mit mehreren Rollout-Projekten vertreten und setzt auf eine ganzheitliche Herangehensweise – von der Store- und Designentwicklung über Planung, Fertigung und Montage bis hin zu digitalen Innovationen und Services im laufenden Betrieb.",
    ],
    "https://www.umdasch.com/de/news/von-der-finanzverwaltung-zum-lebendigen-mixed-use-konzept-die-revitalisierung-der-kaemmerei-duesseldorf": [
        "Zum umfangreichen Leistungspaket zählten unter anderem Trockenbau-, Maler- und Bodenarbeiten, die Installation von Glastrennwänden, Decken- und Türsystemen sowie Fliesen-, Naturstein- und Metallbauarbeiten.",
    ],
    "https://www.umdasch.com/karriere/offene-stellen": [
        "Projektleiter / Projektmanager (m/w/d) Ladenbau",
    ],
}

STEINEL: dict[str, list[str]] = {
    "https://www.steinel.de/de/unternehmen/ueber-steinel/kontakt/": [
        "Einzigartige Projekte, realisiert mit STEINEL Produkten.",
        "OEM-Lösungen",
    ],
}

DEUTSCHE_BANK: dict[str, list[str]] = {
    "https://www.db.com:443/what-next/european-success-stories/european-scale-ups/index?language_id=1&kid=wn.newsteaser.scale-ups.en": [
        "Founded in 1913, the German company Claas is not only one of the world’s leading manufacturers of agricultural machinery – it also provides the systems and the controls behind them.",
    ],
}
