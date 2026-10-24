"""Einheitliche, erweiterbare Schnittstelle für Quellenadapter.

Ein Adapter kann Kandidaten finden (Schritt 2) und/oder eine Firma untersuchen (Schritt 3).
Neue Anbieter: Klasse von ``BaseAdapter`` ableiten, Quelle in ``sources/registry.py``
eintragen und in ``ADAPTERS`` registrieren – Pipeline, Budget, Bericht bleiben unverändert.
Ein Adapter darf nie den ganzen Auftrag abbrechen: Fehler werden als ``Outcome`` gemeldet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import insert, select

from .. import db, repo
from ..context import Ctx
from ..domain import EvidenceDraft
from ..resolution import name_similarity, normalize_name, qualified_name_match
from ..sources import companies_house, eurostat, gdelt, gleif, sec_edgar, ted, website, wikidata
from ..sources.registry import SOURCE_BY_KEY
from ..usecases import UseCase
from .derive import RULES_VERSION, store_fit
from .strategy import SearchStrategy


class Kind(StrEnum):
    """Die zehn Quellentypen aus der Anforderung."""

    WEB_SEARCH = "1_websuche"
    COMPANY_WEBSITE = "2_unternehmenswebseiten"
    ANNUAL_REPORTS = "3_geschaeftsberichte_ir"
    FILINGS = "4_jahresabschluesse_register"
    REGISTERS = "5_register_national_international"
    JOBS = "6_stellenanzeigen"
    NEWS = "7_presse_nachrichten"
    ASSOCIATIONS = "8_verbaende_messen"
    STATISTICS = "9_statistik"
    LICENSED = "10_lizenzierte_anbieter"


@dataclass(slots=True)
class Candidate:
    """Gefundener Kandidat mit Begründung und Beleg."""

    company_id: int
    reason: str
    evidence: EvidenceDraft | None = None
    role: str = "candidate"


@dataclass(slots=True)
class Outcome:
    """Ergebnis einer Untersuchung."""

    status: str  # ok | failed | skipped | not_applicable
    detail: str = ""
    found: int = 0
    documents: list[int] = field(default_factory=list)


class BaseAdapter:
    """Standardverhalten: nichts finden, nichts untersuchen."""

    key = "base"
    source_keys: tuple[str, ...] = ()
    kinds: frozenset[Kind] = frozenset()
    finds = False
    investigates = False

    def enabled(self, ctx: Ctx) -> bool:
        """Alle zugehörigen Quellen sind im Gateway aktiv."""
        return all(k in ctx.gateway.enabled_sources for k in self.source_keys)

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        return []

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        return Outcome("not_applicable")


def _ids(ctx: Ctx, company_id: int) -> dict[str, str]:
    with ctx.engine.connect() as conn:
        return repo.identifiers_of(conn, company_id)


def _company(ctx: Ctx, company_id: int) -> dict[str, object]:
    with ctx.engine.connect() as conn:
        return repo.company_row(conn, company_id)


def _use_case_for_term(term: str, strategy: SearchStrategy) -> str | None:
    origin = strategy.origin.get(term, "")
    return origin.split(":", 1)[1] if origin.startswith("use_case:") else None


# ---------------------------------------------------------------------- Register


class GleifAdapter(BaseAdapter):
    key = "gleif"
    source_keys = ("gleif",)
    kinds = frozenset({Kind.REGISTERS})
    finds = investigates = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        out: list[Candidate] = []
        for term in strategy.name_terms:
            for country in strategy.countries:
                records, doc_id = gleif.search(
                    ctx, term, country=country, limit=ctx.budget.max_search_hits
                )
                for record in records:
                    if doc_id is None or normalize_name(term) not in normalize_name(
                        record.legal_name
                    ):
                        continue
                    company_id = gleif.store_record(ctx, record, doc_id)
                    evidence = EvidenceDraft(
                        doc_id,
                        f"data[lei={record.lei}].entity.legalName",
                        record.legal_name,
                        "api_field",
                        RULES_VERSION,
                        True,
                    )
                    use_case = _use_case_for_term(term, strategy)
                    if use_case:
                        store_fit(
                            ctx,
                            company_id,
                            use_case,
                            "plausible",
                            f"Rechtlicher Name enthält „{term}“",
                            [evidence],
                            RULES_VERSION,
                        )
                    out.append(
                        Candidate(company_id, f"Firmenname enthält „{term}“ ({country})", evidence)
                    )
        return out

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        lei = _ids(ctx, company_id).get("lei")
        if not lei:
            return Outcome("not_applicable", "kein LEI bekannt")
        record, doc_id = gleif.fetch_by_lei(ctx, lei)
        if record is None or doc_id is None:
            return Outcome("failed", f"LEI-Datensatz {lei} nicht abrufbar")
        gleif.store_record(ctx, record, doc_id)
        gleif.enrich_parents(ctx, company_id, lei)
        return Outcome("ok", f"LEI {lei}", 1, [doc_id])


class WikidataAdapter(BaseAdapter):
    key = "wikidata"
    source_keys = ("wikidata",)
    kinds = frozenset({Kind.REGISTERS})
    finds = investigates = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        qids: list[str] = []
        for term in strategy.industry_terms[:6]:
            qids += [h.qid for h in wikidata.search_entities(ctx, term, "de")[:3]]
        qids = sorted(set(qids))
        out: list[Candidate] = []
        for country in strategy.countries:
            rows, doc_id = wikidata.companies_by_industry(
                ctx, qids, country, limit=ctx.budget.max_search_hits
            )
            for row in rows:
                company_id = wikidata.ingest_entity(ctx, row["qid"])
                if company_id is None or doc_id is None:
                    continue
                evidence = EvidenceDraft(
                    doc_id,
                    "results.bindings[item]",
                    f"http://www.wikidata.org/entity/{row['qid']}",
                    "api_field",
                    RULES_VERSION,
                    True,
                )
                for use_case in strategy.use_cases:
                    store_fit(
                        ctx,
                        company_id,
                        use_case,
                        "weak",
                        f"Branchenzuordnung in Wikidata ({', '.join(qids)})",
                        [evidence],
                        RULES_VERSION,
                    )
                out.append(Candidate(company_id, f"Wikidata-Branche ({country})", evidence))
        return out

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        ids = _ids(ctx, company_id)
        if "wikidata" in ids:
            result = wikidata.ingest_entity(ctx, ids["wikidata"], company_id=company_id)
            return Outcome("ok" if result else "failed", ids["wikidata"])
        if "lei" not in ids:
            return Outcome("not_applicable", "weder QID noch LEI bekannt")
        name = str(_company(ctx, company_id)["display_name"])
        for hit in wikidata.search_entities(ctx, name, "de")[:5]:
            entity, _, _ = wikidata.load_entity(ctx, hit.qid)
            if entity and ids["lei"] in wikidata.string_values(entity, "P1278"):
                wikidata.ingest_entity(ctx, hit.qid, company_id=company_id)
                return Outcome("ok", f"{hit.qid} über LEI zugeordnet", 1)
        return Outcome("ok", "kein Eintrag mit passendem LEI gefunden", 0)


class SecEdgarAdapter(BaseAdapter):
    key = "sec_edgar"
    source_keys = ("sec_edgar",)
    kinds = frozenset({Kind.FILINGS})
    investigates = True

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        ids = _ids(ctx, company_id)
        if "sec_cik" in ids:
            created = sec_edgar.ingest_facts(ctx, company_id, int(ids["sec_cik"]))
            return Outcome("ok", f"CIK {ids['sec_cik']}", created)
        country = _company(ctx, company_id)["country_code"]
        if country not in (None, "US"):
            return Outcome("not_applicable", "nicht SEC-meldepflichtig (Sitz außerhalb USA)")
        candidates = sec_edgar.name_candidates(ctx, str(_company(ctx, company_id)["display_name"]))
        for c in candidates:
            ctx.note(f"SEC-Kandidat zur Prüfung: {c.title} (CIK {c.cik}) für Firma {company_id}")
        return Outcome("ok", f"keine CIK über starke Kennung; {len(candidates)} Namenskandidaten")


class CompaniesHouseAdapter(BaseAdapter):
    key = "companies_house"
    source_keys = ("companies_house",)
    kinds = frozenset({Kind.REGISTERS, Kind.FILINGS})
    finds = investigates = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        if "GB" not in strategy.countries:
            return []
        out = []
        for term in strategy.name_terms:
            found, doc_id = companies_house.search(ctx, term, ctx.budget.max_search_hits)
            for company in found:
                if doc_id is None or normalize_name(term) not in normalize_name(company.name):
                    continue
                company_id = companies_house.store(ctx, company, doc_id)
                out.append(
                    Candidate(
                        company_id,
                        f"UK-Register: Name enthält „{term}“",
                        EvidenceDraft(
                            doc_id,
                            f"items[{company.number}]",
                            company.name,
                            "api_field",
                            RULES_VERSION,
                            True,
                        ),
                    )
                )
        return out

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        number = _ids(ctx, company_id).get("uk_crn")
        if not number:
            return Outcome("not_applicable", "keine UK-Registernummer")
        company, doc_id = companies_house.fetch_profile(ctx, number)
        if company is None or doc_id is None:
            return Outcome("failed", f"Profil {number} nicht abrufbar")
        companies_house.store(ctx, company, doc_id)
        return Outcome(
            "ok", f"Status {company.status}, SIC {', '.join(company.sic_codes)}", 1, [doc_id]
        )


# ---------------------------------------------------------------------- Webseiten


class WebsiteAdapter(BaseAdapter):
    key = "website"
    source_keys = ("website",)
    kinds = frozenset({Kind.COMPANY_WEBSITE, Kind.ANNUAL_REPORTS, Kind.JOBS, Kind.NEWS})
    investigates = True

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        domain = _company(ctx, company_id)["website_domain"]
        if not domain:
            return Outcome("not_applicable", "keine Website bekannt (keine Websuche verfügbar)")
        result = website.fetch_site(ctx, str(domain))
        for error in result.errors[:5]:
            ctx.note(f"Website {domain}: {error}")
        if not result.documents:
            return Outcome("failed", "; ".join(result.errors[:2]) or "keine Seite abrufbar")
        pdfs = sum(d.kind == "pdf" for d in result.documents)
        return Outcome(
            "ok",
            f"{len(result.documents) - pdfs} Seiten, {pdfs} PDF",
            len(result.documents),
            [d.document_id for d in result.documents],
        )


# ---------------------------------------------------------------------- Signale


class TedAdapter(BaseAdapter):
    key = "ted"
    source_keys = ("ted",)
    kinds = frozenset({Kind.NEWS})
    finds = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        out = []
        for key in strategy.use_cases:
            for term in cases[key].tender_terms:
                for country in strategy.countries:
                    notices, doc_id, _ = ted.search(
                        ctx, term, country, limit=ctx.budget.max_search_hits
                    )
                    if doc_id is None:
                        continue
                    for company_id, notice in ted.ingest_notices(ctx, notices, doc_id, key):
                        evidence = EvidenceDraft(
                            doc_id,
                            f"notices[publication-number={notice.number}]",
                            notice.title[:300],
                            "api_field",
                            RULES_VERSION,
                            True,
                        )
                        store_fit(
                            ctx,
                            company_id,
                            key,
                            "weak",
                            f"Öffentlicher Auftraggeber schreibt aus („{term}“); Kunde von "
                            "Yellotools ist eher der ausführende Betrieb",
                            [evidence],
                            RULES_VERSION,
                        )
                        out.append(Candidate(company_id, f"Ausschreibung „{term}“", evidence))
        return out


class GdeltAdapter(BaseAdapter):
    key = "gdelt"
    source_keys = ("gdelt",)
    kinds = frozenset({Kind.NEWS})
    finds = investigates = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        for key in strategy.use_cases:
            for term in cases[key].news_terms[:1]:
                for country in strategy.countries[:3]:
                    articles, doc_id, _ = gdelt.search(ctx, term, country)
                    if doc_id is not None:
                        gdelt.ingest_articles(ctx, articles, doc_id, key, country)
        return []  # Nachrichten liefern Signale, keine eigenständigen Kandidaten

    def investigate(
        self, ctx: Ctx, company_id: int, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> Outcome:
        name = str(_company(ctx, company_id)["display_name"])
        country = str(_company(ctx, company_id)["country_code"] or "")
        if len(normalize_name(name)) < 4:
            return Outcome("not_applicable", "Name zu kurz für eindeutige Nachrichtensuche")
        articles, doc_id, _ = gdelt.search(ctx, f"{name}", country or None, timespan="12m")
        if doc_id is None:
            return Outcome("failed", "Nachrichtensuche nicht verfügbar")
        stored = gdelt.ingest_articles(ctx, articles, doc_id, None, country or None)
        return Outcome("ok", f"{len(articles)} Meldungen, {stored} zugeordnet", stored)


# ---------------------------------------------------------------------- Verbände, Statistik


class UserListAdapter(BaseAdapter):
    key = "user_list"
    source_keys = ("user_list",)
    kinds = frozenset({Kind.ASSOCIATIONS})
    finds = True

    def enabled(self, ctx: Ctx) -> bool:
        return True  # lokale Daten, kein Netzabruf

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        if not strategy.lists:
            return []
        out = []
        with ctx.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(db.list_membership).where(
                        db.list_membership.c.list_name.in_(strategy.lists)
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            evidence = EvidenceDraft(
                row["source_document_id"],
                f"Liste {row['list_name']}",
                row["row_text"],
                "user_list",
                RULES_VERSION,
                True,
            )
            out.append(
                Candidate(
                    row["company_id"], f"Firmenliste „{row['list_name']}“", evidence, "listed"
                )
            )
        return out


class EurostatAdapter(BaseAdapter):
    key = "eurostat"
    source_keys = ("eurostat",)
    kinds = frozenset({Kind.STATISTICS})
    finds = True

    def find_candidates(
        self, ctx: Ctx, strategy: SearchStrategy, cases: dict[str, UseCase]
    ) -> list[Candidate]:
        for code in strategy.nace:
            for country in strategy.countries[:5]:
                _, problem = eurostat.fetch(ctx, code, country)
                if problem:
                    ctx.note(f"Eurostat NACE {code} {country}: {problem}")
        return []  # Marktkontext, keine Firmen


ADAPTERS: tuple[BaseAdapter, ...] = (
    GleifAdapter(),
    WikidataAdapter(),
    CompaniesHouseAdapter(),
    UserListAdapter(),
    TedAdapter(),
    GdeltAdapter(),
    EurostatAdapter(),
    SecEdgarAdapter(),
    WebsiteAdapter(),
)
ADAPTER_BY_KEY = {a.key: a for a in ADAPTERS}


def register_candidate(ctx: Ctx, run_id: int, candidate: Candidate, adapter_key: str) -> None:
    """Kandidat dem Auftrag zuordnen (je Quelle und Begründung einmal)."""
    with ctx.engine.begin() as conn:
        exists = conn.execute(
            select(db.run_candidate.c.id).where(
                db.run_candidate.c.run_id == run_id,
                db.run_candidate.c.company_id == candidate.company_id,
                db.run_candidate.c.adapter_key == adapter_key,
                db.run_candidate.c.reason == candidate.reason[:500],
            )
        ).first()
        if exists:
            return
        evidence_id = repo.add_evidence(conn, candidate.evidence) if candidate.evidence else None
        conn.execute(
            insert(db.run_candidate).values(
                run_id=run_id,
                company_id=candidate.company_id,
                adapter_key=adapter_key,
                role=candidate.role,
                reason=candidate.reason[:500],
                evidence_id=evidence_id,
                selected=False,
                found_at=db.utcnow(),
            )
        )


# ---------------------------------------------------------------------- benannte Firmen


GLEIF_MIN_SIMILARITY = 0.85
WIKIDATA_MIN_SIMILARITY = 0.95


def _names(record: gleif.GleifRecord) -> tuple[str, ...]:
    return (record.legal_name, *record.other_names)


def _gleif_named(
    ctx: Ctx, query: str, country: str | None, notes: list[str]
) -> tuple[gleif.GleifRecord | None, int | None]:
    """Rechtsnamen-Suche bei GLEIF, erst im Auftragsland, dann weltweit (Sitz im Ausland).

    Gleicher Name (Ähnlichkeit ≥ 0,85) vor Name mit Länder-/Konzernzusatz; mehrere
    gleichwertige Treffer werden nur über eine gemeinsame, gleichnamige Konzernmutter
    aufgelöst – sonst bleibt die Firma offen (kein Raten).
    """
    for scope in [country, None] if country else [None]:
        records, doc_id = gleif.search(
            ctx, query, country=scope, limit=20, field="entity.legalName"
        )
        if doc_id is None:  # Abruf fehlgeschlagen (protokolliert)
            continue
        active = [r for r in records if (r.entity_status or "ACTIVE") == "ACTIVE"]
        exact = [
            r
            for r in active
            if max(name_similarity(query, n) for n in _names(r)) >= GLEIF_MIN_SIMILARITY
        ]
        pool = exact or [
            r for r in active if any(qualified_name_match(query, n) for n in _names(r))
        ]
        where = f"Land {scope}" if scope else "weltweit"
        if not pool:
            notes.append(f"GLEIF ({where}): kein Rechtsname passt zu „{query}“")
            continue
        if len(pool) == 1:
            if not exact:
                notes.append(
                    f"GLEIF: „{query}“ als {pool[0].legal_name} ({pool[0].lei}, "
                    f"{pool[0].country}) übernommen – Name mit Länder-/Konzernzusatz"
                )
            return pool[0], doc_id
        parent, parent_doc, _ = gleif.fetch_parent(ctx, pool[0].lei, "ultimate")
        if (
            parent is not None
            and parent_doc is not None
            and any(name_similarity(query, n) >= GLEIF_MIN_SIMILARITY for n in _names(parent))
        ):
            notes.append(
                f"GLEIF: {len(pool)} gleichnamige Gesellschaften ({where}); übernommen wurde "
                f"die oberste Konzernmutter {parent.legal_name} ({parent.lei})"
            )
            return parent, parent_doc
        listed = "; ".join(f"{r.legal_name} ({r.lei}, {r.country})" for r in pool[:5])
        notes.append(f"GLEIF ({where}): mehrdeutig, nicht übernommen – {listed}")
        return None, None
    return None, None


def resolve_named(ctx: Ctx, query: str, countries: list[str]) -> tuple[int | None, list[str]]:
    """Ausdrücklich genannte Firma finden: GLEIF (Ähnlichkeit ≥ 0,85), Wikidata (gleicher LEI
    oder neue Firma bei Ähnlichkeit ≥ 0,95), nur starke Kennungen für Zusammenführungen."""
    notes: list[str] = []
    company_id: int | None = None
    lei: str | None = None
    country = countries[0] if len(countries) == 1 else None
    if "gleif" in ctx.gateway.enabled_sources:
        record, doc_id = _gleif_named(ctx, query, country, notes)
        if record is not None and doc_id is not None:
            company_id = gleif.store_record(ctx, record, doc_id)
            lei = record.lei
    if "wikidata" in ctx.gateway.enabled_sources:
        hits = wikidata.search_entities(ctx, query, "de") or wikidata.search_entities(
            ctx, query, "en"
        )
        for hit in hits[:5]:
            entity, _, _ = wikidata.load_entity(ctx, hit.qid)
            if entity is None:
                continue
            if lei and lei in wikidata.string_values(entity, "P1278"):
                wikidata.ingest_entity(ctx, hit.qid, company_id=company_id)
                break
            company_like = any(
                wikidata.claim_values(entity, p)
                for p in ("P1278", "P5531", "P452", "P1128", "P2139")
            )
            similar = name_similarity(query, wikidata.label(entity)) >= WIKIDATA_MIN_SIMILARITY
            if company_id is None and company_like and similar:
                company_id = wikidata.ingest_entity(ctx, hit.qid)
                break
            if company_like and similar:
                notes.append(
                    f"Wikidata: {wikidata.label(entity)} ({hit.qid}) ohne gemeinsamen "
                    "LEI – nicht zusammengeführt (Prüfung nötig)"
                )
    if company_id is None:
        notes.append(f"„{query}“: keine Firma eindeutig gefunden")
    return company_id, notes


def adapter_overview() -> list[dict[str, object]]:
    """Für Dokumentation und ``mi adapters``: welche Funktion welche Quelle hat."""
    rows = []
    for adapter in ADAPTERS:
        sources = [SOURCE_BY_KEY[k] for k in adapter.source_keys]
        rows.append(
            {
                "adapter": adapter.key,
                "kinds": sorted(k.value for k in adapter.kinds),
                "findet_kandidaten": adapter.finds,
                "untersucht_firmen": adapter.investigates,
                "quellen": [s.name for s in sources],
                "rechtsstatus": [s.legal_status for s in sources],
            }
        )
    return rows
