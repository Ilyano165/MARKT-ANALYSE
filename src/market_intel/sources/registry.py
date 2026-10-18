"""Quellenregister: jede Quelle mit Lizenz, kostenlosem Umfang, Grenzen und Rechtsstatus.

Nur Quellen mit ``legal_status`` ``approved`` oder ``approved_with_conditions`` und
``enabled`` werden vom FetchGateway abgerufen. Kostenpflichtige Anbieter stehen nur zur
Transparenz im Register, haben keinen aktiven Adapter und sind immer deaktiviert.

Die Angaben spiegeln den Recherchestand 2026-10-18 (siehe RESEARCH_SOURCES.md, Spalte
Prüfstand) und müssen vor produktivem Einsatz gegen die jeweiligen Bedingungen geprüft werden.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import ProvenanceClass

APPROVED = ("approved", "approved_with_conditions")


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """Stammdaten einer Quelle."""

    key: str
    name: str
    publisher: str
    base_url: str
    source_type: str
    default_provenance: ProvenanceClass
    license: str
    terms_url: str
    free_scope: str
    restrictions: str
    registration: str
    credentials: str
    optional_costs: str
    legal_status: str
    enabled_by_default: bool
    min_interval_seconds: float
    implemented: bool

    @property
    def fetchable(self) -> bool:
        """Darf grundsätzlich abgerufen werden (Rechtsstatus)."""
        return self.legal_status in APPROVED and self.implemented


SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        key="gleif",
        name="GLEIF LEI API",
        publisher="Global Legal Entity Identifier Foundation",
        base_url="https://api.gleif.org/api/v1",
        source_type="register",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="CC0 1.0 (laut Wiederverwendern; GLEIF LEI Data Terms of Use prüfen)",
        terms_url="https://www.gleif.org/en/meta/lei-data-terms-of-use",
        free_scope="vollständig kostenlos, ohne Schlüssel",
        restrictions="nur Unternehmen mit LEI; Mutterbeziehungen = Konsolidierung, ohne Prozente",
        registration="keine",
        credentials="keine",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=True,
        min_interval_seconds=1.0,
        implemented=True,
    ),
    SourceDefinition(
        key="wikidata",
        name="Wikidata (Action API, EntityData, SPARQL)",
        publisher="Wikimedia Foundation / Wikidata-Community",
        base_url="https://www.wikidata.org",
        source_type="open_knowledge_base",
        default_provenance=ProvenanceClass.THIRD_PARTY,
        license="CC0 1.0 (strukturierte Daten)",
        terms_url="https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_API_Usage_Guidelines",
        free_scope="kostenlos; SPARQL: 60 s Rechenzeit je Minute und Client, 60 s Timeout",
        restrictions="User-Agent mit Kontakt Pflicht; lückenhaft bei KMU; Werte oft ohne Beleg",
        registration="keine",
        credentials="keine",
        optional_costs="Wikimedia Enterprise nur für Großvolumen (nicht benötigt)",
        legal_status="approved",
        enabled_by_default=True,
        min_interval_seconds=1.0,
        implemented=True,
    ),
    SourceDefinition(
        key="sec_edgar",
        name="SEC EDGAR (company_tickers, XBRL companyfacts)",
        publisher="U.S. Securities and Exchange Commission",
        base_url="https://data.sec.gov",
        source_type="regulator_filing",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="gemeinfrei (US-Regierungswerk)",
        terms_url="https://www.sec.gov/os/accessing-edgar-data",
        free_scope="kostenlos, max. 10 Anfragen/s",
        restrictions="nur SEC-meldepflichtige Firmen; User-Agent mit Firma und E-Mail Pflicht",
        registration="keine",
        credentials="keine (aber Kontakt-E-Mail im User-Agent)",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=True,
        min_interval_seconds=0.2,
        implemented=True,
    ),
    SourceDefinition(
        key="companies_house",
        name="UK Companies House Public Data API",
        publisher="Companies House (UK)",
        base_url="https://api.company-information.service.gov.uk",
        source_type="register",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="Open Government Licence (Daten); Entwicklerbedingungen beachten",
        terms_url="https://developer.company-information.service.gov.uk/developer-guidelines",
        free_scope="kostenlos, 600 Anfragen je 5 Minuten",
        restrictions="nur UK; keine Mitarbeiterzahlen; Abschlüsse als Dokumente",
        registration="kostenloses Konto im Developer Hub",
        credentials="kostenloser API-Schlüssel (Umgebungsvariable MI_COMPANIES_HOUSE_KEY)",
        optional_costs="keine (beglaubigte Kopien kostenpflichtig, nicht genutzt)",
        legal_status="approved",
        enabled_by_default=False,
        min_interval_seconds=0.6,
        implemented=True,
    ),
    SourceDefinition(
        key="ecb_fx",
        name="EZB Data Portal – Referenzwechselkurse",
        publisher="Europäische Zentralbank",
        base_url="https://data-api.ecb.europa.eu",
        source_type="official_statistics",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="Wiederverwendung mit Quellenangabe „Source: ECB statistics“ (zu prüfen)",
        terms_url="https://www.ecb.europa.eu/home/disclaimer/html/index.en.html",
        free_scope="kostenlos, ohne Schlüssel",
        restrictions="nur Kurse; Daten unverändert weitergeben",
        registration="keine",
        credentials="keine",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=True,
        min_interval_seconds=1.0,
        implemented=True,
    ),
    SourceDefinition(
        key="ted",
        name="TED Search API v3 (EU-Ausschreibungen)",
        publisher="Amt für Veröffentlichungen der EU",
        base_url="https://api.ted.europa.eu/v3",
        source_type="tender_portal",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="Wiederverwendung nach Beschluss 2011/833/EU mit Quellenangabe (zu prüfen)",
        terms_url="https://ted.europa.eu/en/simap/developers-corner-for-reusers",
        free_scope="kostenlos, anonym; max. 100 Treffer je Seite",
        restrictions="nur öffentliche Auftraggeber; Gewinner nicht in jeder Bekanntmachung",
        registration="keine",
        credentials="keine",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=True,
        min_interval_seconds=1.0,
        implemented=True,
    ),
    SourceDefinition(
        key="gdelt",
        name="GDELT DOC 2.0 API (Nachrichtensuche)",
        publisher="The GDELT Project",
        base_url="https://api.gdeltproject.org/api/v2",
        source_type="press",
        default_provenance=ProvenanceClass.THIRD_PARTY,
        license="laut GDELT-Bedingungen frei nutzbar mit Zitat und Link (zu prüfen); "
        "Artikeltexte gehören den Verlagen",
        terms_url="https://www.gdeltproject.org/about.html#termsofuse",
        free_scope="kostenlos, ohne Schlüssel; gedrosselt (Richtwert 1 Anfrage je 5 s)",
        restrictions="nur Titel/URL/Datum gespeichert; Artikel nicht kopieren",
        registration="keine",
        credentials="keine",
        optional_costs="keine (GDELT Cloud ist ein separates kommerzielles Produkt, nicht genutzt)",
        legal_status="approved_with_conditions",
        enabled_by_default=True,
        min_interval_seconds=5.0,
        implemented=True,
    ),
    SourceDefinition(
        key="website",
        name="Unternehmenswebseiten",
        publisher="jeweiliges Unternehmen",
        base_url="https://",
        source_type="company_website",
        default_provenance=ProvenanceClass.COMPANY_REPORTED,
        license="urheberrechtlich geschützt; TDM nach § 44b UrhG ohne Nutzungsvorbehalt",
        terms_url="https://www.gesetze-im-internet.de/urhg/__44b.html",
        free_scope="kostenlos; max. Seiten je Domain konfigurierbar (Standard 8)",
        restrictions="robots.txt und TDM-Vorbehalt verbindlich; kein Login, keine Formulare",
        registration="keine",
        credentials="keine",
        optional_costs="keine",
        legal_status="approved_with_conditions",
        enabled_by_default=True,
        min_interval_seconds=2.0,
        implemented=True,
    ),
    SourceDefinition(
        key="eurostat",
        name="Eurostat Dissemination API (SBS)",
        publisher="Eurostat",
        base_url="https://ec.europa.eu/eurostat/api/dissemination",
        source_type="official_statistics",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="Eurostat-Wiederverwendung mit Quellenangabe",
        terms_url="https://ec.europa.eu/eurostat/about-us/policies/copyright",
        free_scope="kostenlos, ohne Schlüssel",
        restrictions="aggregierte Statistik, keine Einzelfirmen; NACE-Bruch ab 2025",
        registration="keine",
        credentials="keine",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=False,
        min_interval_seconds=1.0,
        implemented=False,
    ),
    SourceDefinition(
        key="destatis",
        name="Destatis GENESIS-Online Webservice",
        publisher="Statistisches Bundesamt",
        base_url="https://www-genesis.destatis.de/genesisWS/rest/2020",
        source_type="official_statistics",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="Datenlizenz Deutschland – Namensnennung 2.0",
        terms_url="https://www.destatis.de/EN/Service/OpenData/api-webservice.html",
        free_scope="kostenlos",
        restrictions="aggregierte Statistik; API-Token laut Dritt-Doku über kostenloses Konto",
        registration="kostenloses Konto (für Token, zu prüfen)",
        credentials="kostenloser Token",
        optional_costs="keine",
        legal_status="approved",
        enabled_by_default=False,
        min_interval_seconds=1.0,
        implemented=False,
    ),
    SourceDefinition(
        key="ba_jobsuche",
        name="Bundesagentur für Arbeit – Jobsuche",
        publisher="Bundesagentur für Arbeit",
        base_url="https://rest.arbeitsagentur.de",
        source_type="job_board",
        default_provenance=ProvenanceClass.THIRD_PARTY,
        license="ungeklärt",
        terms_url="https://www.arbeitsagentur.de",
        free_scope="technisch frei erreichbar",
        restrictions="keine offizielle API (nur Community-Doku); Nutzung ungeklärt",
        registration="keine",
        credentials="öffentlich bekannter Client-Schlüssel",
        optional_costs="keine",
        legal_status="pending_review",
        enabled_by_default=False,
        min_interval_seconds=2.0,
        implemented=False,
    ),
    SourceDefinition(
        key="handelsregister",
        name="Handelsregister (handelsregister.de)",
        publisher="Länderjustizverwaltungen",
        base_url="https://www.handelsregister.de",
        source_type="register",
        default_provenance=ProvenanceClass.OFFICIAL,
        license="amtlich",
        terms_url="https://www.handelsregister.de",
        free_scope="Einzelabrufe im Browser",
        restrictions="max. 60 Abrufe/h; automatisierte Massenabfragen untersagt",
        registration="keine",
        credentials="keine",
        optional_costs="Dokumentabrufe teils gebührenpflichtig",
        legal_status="blocked",
        enabled_by_default=False,
        min_interval_seconds=60.0,
        implemented=False,
    ),
    SourceDefinition(
        key="marginalia",
        name="Marginalia Search API",
        publisher="Marginalia Search",
        base_url="https://api2.marginalia-search.com",
        source_type="search_engine",
        default_provenance=ProvenanceClass.THIRD_PARTY,
        license="kostenloser Schlüssel nur nicht-kommerziell (CC BY-NC-SA 4.0)",
        terms_url="https://about.marginalia-search.com/article/api",
        free_scope="Public Key stark gedrosselt; Schlüssel per E-Mail nur nicht-kommerziell",
        restrictions="kommerzielle Nutzung nicht vom kostenlosen Schlüssel gedeckt",
        registration="E-Mail-Anfrage",
        credentials="API-Schlüssel",
        optional_costs="kommerzieller Schlüssel kostenpflichtig",
        legal_status="excluded_paid",
        enabled_by_default=False,
        min_interval_seconds=2.0,
        implemented=False,
    ),
    SourceDefinition(
        key="northdata",
        name="North Data Data API (optional, kostenpflichtig)",
        publisher="North Data GmbH",
        base_url="https://www.northdata.de",
        source_type="data_provider",
        default_provenance=ProvenanceClass.THIRD_PARTY,
        license="Vertrag",
        terms_url="https://northdata.de/_agb",
        free_scope="kein kostenloser API-Zugang",
        restrictions="nur mit Vertrag",
        registration="Vertrag",
        credentials="API-Schlüssel",
        optional_costs="ab 500 €/Monat laut Preisseite",
        legal_status="excluded_paid",
        enabled_by_default=False,
        min_interval_seconds=1.0,
        implemented=False,
    ),
)

SOURCE_BY_KEY = {s.key: s for s in SOURCES}


def is_enabled(
    definition: SourceDefinition,
    enabled: tuple[str, ...],
    disabled: tuple[str, ...],
    has_companies_house_key: bool,
) -> bool:
    """Aktiviert = abrufbar + (Standard oder ausdrücklich eingeschaltet) − ausgeschaltet."""
    if not definition.fetchable or definition.key in disabled:
        return False
    if definition.key == "companies_house" and not has_companies_house_key:
        return False
    return definition.enabled_by_default or definition.key in enabled
