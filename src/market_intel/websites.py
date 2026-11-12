"""Website-Ermittlung (Phase G): Kandidaten finden, belegt prüfen, vom Nutzer bestätigen lassen.

Nicht genutzt (geprüft 2026-11-12): OpenStreetMap Nominatim – robots.txt untersagt ``/search``
für automatisierte Zugriffe; Overpass – Hauptinstanz aus der Testumgebung nicht erreichbar,
Ausweichinstanzen mit Zeitüberschreitung bei Namenssuchen; Suchmaschinen-APIs – nur mit
Schlüssel bzw. nicht für kommerzielle Nutzung kostenlos; Branchenbücher – Nutzungsbedingungen
untersagen automatisierte Abfragen.

Ablauf je Unternehmen ohne bestätigte Website:

1. **Kandidaten** aus voneinander unabhängigen Quellen (jede mit eigenem Status im Protokoll):

   * ``bestand``  – bereits gespeicherte, aber nicht bestätigte Website (z. B. aus Wikidata oder
     einer Firmenliste übernommen),
   * ``ted``      – Domain der E-Mail-Adresse aus einer EU-Zuschlagsbekanntmachung (nur wenn
     der Zuschlagsempfänger eindeutig zuzuordnen war; Freemail-Domains ausgeschlossen),
   * ``wikidata`` – Eigenschaft P856 „offizielle Website“ eines namensgleichen Eintrags im
     selben Land,
   * ``ableitung`` – aus dem Firmennamen gebildete Domain. Das ist **kein Fund**, sondern eine
     Vermutung; sie wird nur gezeigt, wenn die Seite selbst den Namen bzw. die Adresse belegt.

2. **Prüfung** jedes Kandidaten: Startseite und Impressum abrufen (robots.txt, Drosselung und
   Sperren über das Gateway). Abgeglichen werden nur Angaben, die zur Firma belegt vorliegen:
   Name, Rechtsform, Registernummer, PLZ/Ort/Straße, Telefon, E-Mail-Domain, Branchenbegriffe,
   Weiterleitungen. Fehlende Angaben gelten als „nicht prüfbar“ – nie als Übereinstimmung.

3. **Entscheidung** durch den Nutzer: bestätigen, ablehnen, andere wählen, manuell eintragen.
   Erst eine bestätigte (oder manuell eingetragene) Website wird für die Unternehmensanalyse
   genutzt. Abgelehnte Vorschläge erscheinen nicht erneut, solange die Suchgrundlage (Name,
   Ort, PLZ, Land) gleich bleibt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from sqlalchemy import Engine, func, insert, select, update

from . import db, repo
from .context import Ctx
from .extraction.text import html_to_text
from .resolution import (
    is_name_match,
    legal_form_of,
    name_similarity,
    normalize_domain,
    normalize_name,
)

log = logging.getLogger("market_intel.websites")
METHOD = "websites-1.0"
PURPOSE = "Website-Ermittlung (Prüfung)"
SOURCE_LABELS = {
    "bestand": "bereits gespeichert (nicht bestätigt)",
    "ted": "EU-Ausschreibung (Kontakt-E-Mail des Zuschlagsempfängers)",
    "wikidata": "Wikidata (offizielle Website, P856)",
    "ableitung": "aus dem Firmennamen abgeleitet (Vermutung, durch die Seite selbst zu belegen)",
}
VERDICT_LABELS = {
    "eindeutig": "eindeutige Zuordnung",
    "wahrscheinlich": "wahrscheinlich – bitte prüfen",
    "unklar": "unklar – Belege reichen nicht",
    "widerspricht": "spricht dagegen",
    "nicht_erreichbar": "Seite nicht erreichbar",
}
STATUS_LABELS = {
    "vorgeschlagen": "offen",
    "bestaetigt": "bestätigt",
    "abgelehnt": "abgelehnt",
    "nicht_gewaehlt": "nicht gewählt (andere bestätigt)",
}
WEBSITE_SOURCE_LABELS = {
    "manuell": "manuell eingetragen",
    "bestaetigt": "bestätigt",
}
TLDS = {"DE": ("de", "com"), "AT": ("at", "com"), "CH": ("ch", "com")}
MAX_DERIVED = 4
_IMPRINT = re.compile(
    r"impressum|imprint|legal[-_ ]?notice|kontakt|contact|about|ueber-uns|über-uns", re.I
)
_STOP = frozenset({"und", "and", "der", "die", "das", "inh", "inhaber", "e", "k", "ek", "co", "kg"})


class WebsiteError(ValueError):
    """Unzulässige Entscheidung (mit Begründung)."""


# ---------------------------------------------------------------------- Firmenangaben


@dataclass
class Facts:
    """Belegte Angaben zur Firma, gegen die eine Website geprüft wird."""

    company_id: int
    name: str
    legal_name: str | None
    aliases: list[str]
    country: str | None
    cities: set[str] = field(default_factory=set)
    postal_codes: set[str] = field(default_factory=set)
    streets: set[str] = field(default_factory=set)
    register_numbers: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    email_domains: set[str] = field(default_factory=set)
    qid: str | None = None
    terms: set[str] = field(default_factory=set)  # Branchen-/Tätigkeitsbegriffe (Anwendungsfälle)
    website: str | None = None
    website_source: str | None = None

    @property
    def basis(self) -> str:
        """Fingerabdruck der Suchgrundlage – ändert er sich, wird neu vorgeschlagen."""
        payload = json.dumps(
            [
                normalize_name(self.legal_name or self.name),
                self.country,
                sorted(self.cities),
                sorted(self.postal_codes),
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def facts_of(engine: Engine, company_id: int) -> Facts:
    with engine.connect() as conn:
        row = repo.company_row(conn, company_id)
        ids = conn.execute(
            select(db.company_identifier.c.scheme, db.company_identifier.c.value).where(
                db.company_identifier.c.company_id == company_id
            )
        ).all()
        aliases = [a["alias"] for a in repo.aliases_of(conn, company_id)]
        locations = repo.locations_of(conn, company_id)
        use_cases = {
            r.use_case_key
            for r in conn.execute(
                select(db.company_product_fit.c.use_case_key).where(
                    db.company_product_fit.c.company_id == company_id
                )
            )
        }
    facts = Facts(
        company_id=company_id,
        name=row["display_name"],
        legal_name=row["legal_name"],
        aliases=list(dict.fromkeys(a for a in aliases if a)),
        country=row["country_code"],
        website=row["website_domain"],
        website_source=row.get("website_source"),
    )
    if row.get("registered_city"):
        facts.cities.add(_norm_place(row["registered_city"]))
    for loc in locations:
        if loc.get("city"):
            facts.cities.add(_norm_place(loc["city"]))
        if loc.get("postal_code"):
            facts.postal_codes.add(str(loc["postal_code"]).strip())
        if loc.get("street"):
            facts.streets.add(_norm_street(loc["street"]))
    for scheme, value in ids:
        if scheme == "gleif_ra" and ":" in value:
            facts.register_numbers.add(_norm_register(value.split(":", 1)[1]))
        elif scheme in ("de_register", "handelsregister"):
            facts.register_numbers.add(_norm_register(value))
        elif scheme == "phone":
            facts.phones.add(value)
        elif scheme == "email_domain":
            facts.email_domains.add(value)
        elif scheme == "wikidata":
            facts.qid = value
    if use_cases:
        from .usecases import load_use_cases

        cases = load_use_cases()
        for key in use_cases:
            case = cases.get(key)
            if case is not None:
                facts.terms.update(t.lower() for t in case.website_terms[:12])
    facts.cities.discard("")
    facts.register_numbers.discard("")
    return facts


def _norm_place(text: str) -> str:
    return normalize_name(str(text)).replace(" ", "")


def _norm_street(text: str) -> str:
    text = (
        str(text).lower().replace("straße", "str").replace("strasse", "str").replace("str.", "str")
    )
    return re.sub(r"[^a-z0-9äöüß]", "", text)


def _norm_register(text: str) -> str:
    match = re.search(r"\b(HR[AB])\s*([0-9]{2,7})\s*([A-Z]{0,3})\b", str(text).upper())
    return f"{match.group(1)}{match.group(2)}" if match else ""


# ---------------------------------------------------------------------- Kandidaten finden


@dataclass
class Proposal:
    domain: str
    url: str
    sources: list[dict[str, Any]] = field(default_factory=list)  # {source, detail, document_id}

    def add(self, source: str, detail: str, document_id: int | None = None) -> None:
        if not any(s["source"] == source for s in self.sources):
            self.sources.append({"source": source, "detail": detail, "document_id": document_id})


def _add(
    found: dict[str, Proposal],
    domain: str | None,
    source: str,
    detail: str,
    document_id: int | None = None,
) -> None:
    domain = normalize_domain(domain) if domain else None
    if not domain or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return
    found.setdefault(domain, Proposal(domain, f"https://{domain}/")).add(
        source, detail, document_id
    )


def find_candidates(ctx: Ctx, facts: Facts) -> tuple[list[Proposal], dict[str, dict[str, Any]]]:
    """Kandidaten aus allen Quellen. Rückgabe: (Vorschläge, Protokoll je Quelle)."""
    found: dict[str, Proposal] = {}
    outcome: dict[str, dict[str, Any]] = {}

    # bestand: gespeicherte, nicht bestätigte Website
    if facts.website and facts.website_source not in WEBSITE_SOURCE_LABELS:
        _add(
            found,
            facts.website,
            "bestand",
            f"gespeichert aus {facts.website_source or 'unbekannter Quelle'}",
        )
        outcome["bestand"] = {"status": "ok", "hits": 1}
    else:
        outcome["bestand"] = {"status": "keine", "hits": 0}

    # ted: Domains belegter Kontakt-E-Mails
    for domain in sorted(facts.email_domains):
        _add(found, domain, "ted", f"Kontakt-E-Mail @{domain} in einer Zuschlagsbekanntmachung")
    outcome["ted"] = {
        "status": "ok" if facts.email_domains else "keine",
        "hits": len(facts.email_domains),
    }

    outcome["wikidata"] = _from_wikidata(ctx, facts, found)
    derived = derived_domains(facts)
    for domain in derived:
        if domain not in found:
            _add(found, domain, "ableitung", "aus dem Firmennamen gebildet")
    outcome["ableitung"] = {"status": "ok", "hits": len(derived), "domains": derived}
    return list(found.values()), outcome


def _from_wikidata(ctx: Ctx, facts: Facts, found: dict[str, Proposal]) -> dict[str, Any]:
    from .sources import wikidata

    if "wikidata" not in ctx.gateway.enabled_sources:
        return {"status": "deaktiviert", "hits": 0}
    before = len(ctx.notes)
    qids: list[str] = [facts.qid] if facts.qid else []
    if not qids:
        for hit in wikidata.search_entities(ctx, facts.legal_name or facts.name, limit=5):
            if is_name_match(facts.legal_name or facts.name, hit.label) or any(
                is_name_match(a, hit.label) for a in facts.aliases
            ):
                qids.append(hit.qid)
    errors = ctx.notes[before:]
    hits = 0
    for qid in qids[:3]:
        entity, document_id, _ = wikidata.load_entity(ctx, qid)
        if entity is None:
            continue
        countries = wikidata.item_values(entity, "P17")
        country = wikidata.code_of(ctx, countries[0], "P297") if countries else None
        if facts.country and country and country != facts.country:
            continue  # gleicher Name, anderes Land
        for site in wikidata.string_values(entity, "P856"):
            _add(
                found,
                site,
                "wikidata",
                f"{qid} „{wikidata.label(entity)}“: offizielle Website {site}",
                document_id,
            )
            hits += 1
    errors = ctx.notes[before:]
    if errors and not hits:
        return {"status": "fehler", "hits": 0, "error": "; ".join(errors[-2:])[:300]}
    return {"status": "ok" if hits else "keine", "hits": hits}


def derived_domains(facts: Facts) -> list[str]:
    """Aus dem Firmennamen gebildete Domains (Vermutung, höchstens ``MAX_DERIVED``)."""
    base = normalize_name(facts.legal_name or facts.name)
    tokens = [t for t in re.split(r"[^a-z0-9]+", base) if t and t not in _STOP]
    if not tokens:
        return []
    variants: list[str] = []
    for count in (min(2, len(tokens)), 1, len(tokens)):
        part = tokens[:count]
        if len("".join(part)) < 4:
            continue
        variants += ["".join(part), "-".join(part)]
    tlds = TLDS.get(facts.country or "", ("com",))
    out: list[str] = []
    for variant in dict.fromkeys(variants):
        for tld in tlds:
            domain = f"{variant}.{tld}"
            if domain not in out:
                out.append(domain)
    return out[:MAX_DERIVED]


# ---------------------------------------------------------------------- Prüfen


@dataclass
class Check:
    criterion: str
    result: str  # passt | teilweise | widerspricht | nicht_prüfbar
    detail: str
    points: int = 0


@dataclass
class Assessment:
    domain: str
    url: str
    reachable: bool
    score: int
    verdict: str
    checks: list[Check]
    documents: list[int]
    notes: list[str]
    found_name: str | None = None

    def details(self) -> dict[str, Any]:
        return {
            "method": METHOD,
            "checks": [c.__dict__ for c in self.checks],
            "pro": [c.detail for c in self.checks if c.result in ("passt", "teilweise")],
            "contra": [c.detail for c in self.checks if c.result == "widerspricht"],
            "unchecked": [c.detail for c in self.checks if c.result == "nicht_prüfbar"],
            "notes": self.notes,
            "found_name": self.found_name,
        }


def _get(ctx: Ctx, url: str, attempts: int | None = None) -> tuple[Any, str, list[str]]:
    """Seite abrufen; Weiterleitungen (auch auf andere Domains) werden verfolgt und vermerkt."""
    notes: list[str] = []
    for _ in range(5):
        result = ctx.gateway.get(
            "website",
            url,
            purpose=PURPOSE,
            respect_robots=True,
            title=url,
            max_bytes=3 * 1024 * 1024,
            attempts=attempts,
        )
        if result.location:
            target = urljoin(url, result.location)
            if normalize_domain(target) != normalize_domain(url):  # www. → gleiche Domain
                notes.append(
                    f"Weiterleitung von {urlsplit(url).hostname} auf {urlsplit(target).hostname}"
                )
            url = target
            continue
        return result, url, notes
    return None, url, [*notes, "zu viele Weiterleitungen"]


def _text(result: Any, url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    ctype = (result.content_type or "").lower()
    if "html" not in ctype and "text/plain" not in ctype:
        return "", ()
    parsed = html_to_text(result.text, url)
    return f"{parsed.title}\n{parsed.text}", parsed.links


def verify(ctx: Ctx, facts: Facts, proposal: Proposal) -> Assessment:
    """Kandidaten abrufen und gegen die belegten Firmenangaben prüfen."""
    derived = {s["source"] for s in proposal.sources} == {"ableitung"}
    # Vermutete Domains: ein Versuch genügt – existiert die Domain nicht, ist das kein Ausfall
    attempts = 1 if derived else None
    result, final_url, notes = _get(ctx, proposal.url, attempts)
    # Auch nach einem Verbindungsfehler die www-Variante prüfen: viele Firmen sind nur unter
    # www. erreichbar (bei vermuteten Domains je ein Versuch)
    if (result is None or not result.ok) and not proposal.url.startswith("https://www."):
        alt = f"https://www.{proposal.domain}/"
        result2, final2, notes2 = _get(ctx, alt, attempts)
        if result2 is not None and result2.ok:
            result, final_url, notes = result2, final2, notes + notes2
    final_domain = normalize_domain(final_url) or proposal.domain
    if result is None or not result.ok or result.document_id is None:
        reason = (
            f"{result.status.value} {result.error}".strip() if result is not None else "kein Abruf"
        )
        return Assessment(
            final_domain,
            final_url,
            False,
            0,
            "nicht_erreichbar",
            [Check("Erreichbarkeit", "nicht_prüfbar", f"Seite nicht abrufbar: {reason}")],
            [],
            notes,
        )
    documents = [result.document_id]
    home_text, links = _text(result, final_url)
    imprint_text = ""
    for link, label in links:
        host = urlsplit(link).hostname or ""
        if normalize_domain(host) != final_domain or not _IMPRINT.search(f"{link} {label}"):
            continue
        sub, sub_url, _sub_notes = _get(ctx, link)
        if sub is not None and sub.ok and sub.document_id is not None:
            documents.append(sub.document_id)
            imprint_text += "\n" + _text(sub, sub_url)[0]
        if len(documents) >= 3:
            break
    text = f"{home_text}\n{imprint_text}"
    checks = _checks(facts, proposal, final_domain, text, imprint_text, notes)
    score = max(0, min(100, sum(c.points for c in checks)))
    verdict = _verdict(checks, score)
    found = next(
        (c.detail for c in checks if c.criterion == "Firmenname" and c.result == "passt"), None
    )
    return Assessment(
        final_domain, final_url, True, score, verdict, checks, documents, notes, found
    )


def _flat(text: str) -> str:
    text = text.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return re.sub(r"\s+", " ", text)


def _checks(
    facts: Facts, proposal: Proposal, domain: str, text: str, imprint: str, notes: list[str]
) -> list[Check]:
    flat = _flat(text)
    compact = re.sub(r"[^a-z0-9]", "", flat)
    checks: list[Check] = []
    names = [facts.legal_name or facts.name, facts.name, *facts.aliases]
    target = facts.legal_name or facts.name

    # 1 Firmenname (vollständig mit Rechtsform > Kernname > fehlt)
    exact = next((n for n in names if n and _flat(n) in flat), None)
    core = normalize_name(target)
    core_tokens = [t for t in core.split() if len(t) > 2 and t not in _STOP]
    if exact and legal_form_of(exact):
        checks.append(
            Check("Firmenname", "passt", f"vollständiger Firmenname „{exact}“ auf der Seite", 35)
        )
    elif exact or (core and re.sub(r"[^a-z0-9]", "", core) in compact):
        checks.append(
            Check(
                "Firmenname",
                "teilweise",
                f"Name „{core}“ genannt, Rechtsform nicht im selben Wortlaut",
                18,
            )
        )
    elif core_tokens and sum(t in flat for t in core_tokens) >= max(1, len(core_tokens) - 1):
        checks.append(
            Check(
                "Firmenname",
                "teilweise",
                f"Namensbestandteile ({', '.join(core_tokens)}) einzeln genannt",
                8,
            )
        )
    else:
        checks.append(
            Check(
                "Firmenname",
                "widerspricht",
                f"Firmenname „{target}“ auf Startseite/Impressum nicht gefunden",
                -30,
            )
        )

    # 2 Registernummer
    if facts.register_numbers:
        page_regs = {
            _norm_register(m.group(0)) for m in re.finditer(r"HR[AB]\s*[0-9]{2,7}", text.upper())
        }
        page_regs.discard("")
        if facts.register_numbers & page_regs:
            checks.append(
                Check(
                    "Registernummer",
                    "passt",
                    f"Handelsregisternummer {', '.join(sorted(facts.register_numbers & page_regs))} im Impressum",
                    30,
                )
            )
        elif page_regs:
            checks.append(
                Check(
                    "Registernummer",
                    "widerspricht",
                    f"andere Registernummer auf der Seite ({', '.join(sorted(page_regs))}) als belegt ({', '.join(sorted(facts.register_numbers))})",
                    -25,
                )
            )
        else:
            checks.append(
                Check(
                    "Registernummer", "nicht_prüfbar", "keine Registernummer auf der Seite gefunden"
                )
            )
    else:
        checks.append(
            Check("Registernummer", "nicht_prüfbar", "zur Firma ist keine Registernummer belegt")
        )

    # 3 Standort (PLZ, Ort, Straße)
    if facts.postal_codes or facts.cities:
        plz_hit = sorted(p for p in facts.postal_codes if re.search(rf"\b{re.escape(p)}\b", text))
        city_hit = sorted(c for c in facts.cities if c and c in compact)
        street_hit = sorted(s for s in facts.streets if s and s in compact)
        page_plz = set(re.findall(r"\b\d{5}\b", imprint or text))
        if plz_hit and city_hit:
            pts = 20 + (5 if street_hit else 0)
            checks.append(
                Check(
                    "Standort",
                    "passt",
                    f"PLZ {', '.join(plz_hit)} und Ort stimmen"
                    + (" (auch Straße)" if street_hit else ""),
                    pts,
                )
            )
        elif city_hit:
            checks.append(
                Check(
                    "Standort",
                    "teilweise",
                    f"Ort ({', '.join(city_hit)}) genannt, PLZ nicht bestätigt",
                    8,
                )
            )
        elif facts.postal_codes and page_plz and not plz_hit:
            checks.append(
                Check(
                    "Standort",
                    "widerspricht",
                    f"Seite nennt andere PLZ ({', '.join(sorted(page_plz)[:3])}) als belegt ({', '.join(sorted(facts.postal_codes))})",
                    -20,
                )
            )
        else:
            checks.append(
                Check(
                    "Standort",
                    "nicht_prüfbar",
                    "kein Ort/keine PLZ auf Startseite/Impressum gefunden",
                )
            )
    else:
        checks.append(Check("Standort", "nicht_prüfbar", "zur Firma ist kein Standort belegt"))

    # 4 Kontaktdaten (Telefon, E-Mail-Domain)
    page_digits = re.sub(r"\D", "", text)
    phone_hit = [p for p in facts.phones if p[2:] and p[2:] in page_digits]
    mail_hit = domain in facts.email_domains or any(f"@{d}" in flat for d in facts.email_domains)
    if phone_hit or mail_hit:
        parts = (["Telefonnummer stimmt"] if phone_hit else []) + (
            ["E-Mail-Domain der belegten Kontaktadresse"] if mail_hit else []
        )
        checks.append(
            Check(
                "Kontaktdaten", "passt", ", ".join(parts), 10 * (bool(phone_hit) + bool(mail_hit))
            )
        )
    elif facts.phones or facts.email_domains:
        checks.append(
            Check(
                "Kontaktdaten",
                "nicht_prüfbar",
                "belegte Telefonnummer/E-Mail auf der Seite nicht gefunden",
            )
        )
    else:
        checks.append(
            Check("Kontaktdaten", "nicht_prüfbar", "zur Firma sind keine Kontaktdaten belegt")
        )

    # 5 Branche / Tätigkeit
    if facts.terms:
        hits = sorted(t for t in facts.terms if t in flat)[:5]
        if hits:
            checks.append(
                Check("Branche", "passt", f"Tätigkeitsbegriffe auf der Seite: {', '.join(hits)}", 5)
            )
        else:
            checks.append(
                Check(
                    "Branche", "nicht_prüfbar", "keine der erwarteten Tätigkeitsbegriffe gefunden"
                )
            )
    else:
        checks.append(Check("Branche", "nicht_prüfbar", "zur Firma ist keine Tätigkeit belegt"))

    # 6 Marke / Domain
    domain_core = re.sub(r"[^a-z0-9]", "", domain.rsplit(".", 1)[0])
    name_core = re.sub(r"[^a-z0-9]", "", core)
    if domain_core and name_core and (domain_core in name_core or name_core in domain_core):
        checks.append(
            Check("Markenbezug", "passt", f"Domain „{domain}“ entspricht dem Firmennamen", 5)
        )
    elif name_similarity(domain_core, name_core) >= 0.6:
        checks.append(
            Check("Markenbezug", "teilweise", f"Domain „{domain}“ ähnelt dem Firmennamen", 2)
        )
    else:
        checks.append(
            Check(
                "Markenbezug",
                "nicht_prüfbar",
                f"Domain „{domain}“ weicht vom Firmennamen ab (Marke? nicht belegt)",
            )
        )

    # 7 Weiterleitung / Herkunft
    if domain != proposal.domain:
        checks.append(
            Check(
                "Weiterleitung",
                "widerspricht" if not exact else "teilweise",
                f"{proposal.domain} leitet auf {domain} weiter",
                0,
            )
        )
    sources = {s["source"] for s in proposal.sources}
    if sources & {"wikidata", "ted"}:
        checks.append(
            Check(
                "Quelle",
                "passt",
                "von einer unabhängigen Quelle genannt: "
                + ", ".join(sorted(sources - {"ableitung", "bestand"}) or sources),
                10,
            )
        )
    elif sources == {"ableitung"}:
        checks.append(
            Check(
                "Quelle",
                "nicht_prüfbar",
                "nur aus dem Namen abgeleitet – keine Quelle nennt diese Website",
            )
        )
    return checks


def _verdict(checks: list[Check], score: int) -> str:
    by = {c.criterion: c for c in checks}
    contra = [c for c in checks if c.result == "widerspricht"]
    name = by.get("Firmenname")
    strong = [
        c
        for c in checks
        if c.criterion in ("Registernummer", "Standort", "Kontaktdaten") and c.result == "passt"
    ]
    if name is not None and name.result == "widerspricht":
        return "widerspricht"
    if contra and any(c.criterion in ("Registernummer", "Standort") for c in contra):
        return "widerspricht"
    if score >= 70 and name is not None and name.result == "passt" and strong and not contra:
        return "eindeutig"
    if score >= 40 and not contra:
        return "wahrscheinlich"
    return "unklar"


# ---------------------------------------------------------------------- Ablauf je Firma


def discover(ctx: Ctx, company_id: int, *, run_id: int | None = None) -> dict[str, Any]:
    """Website-Kandidaten suchen, prüfen und speichern. Gibt eine Zusammenfassung zurück."""
    engine = ctx.engine
    facts = facts_of(engine, company_id)
    basis = facts.basis
    if facts.website and facts.website_source in WEBSITE_SOURCE_LABELS:
        return {"company_id": company_id, "status": "bestätigt", "domain": facts.website}
    proposals, outcome = find_candidates(ctx, facts)
    with engine.connect() as conn:
        decided = {
            r.domain: (r.status, r.search_basis)
            for r in conn.execute(
                select(
                    db.website_candidate.c.domain,
                    db.website_candidate.c.status,
                    db.website_candidate.c.search_basis,
                ).where(db.website_candidate.c.company_id == company_id)
            )
        }
    stored: list[str] = []
    skipped: list[str] = []
    for proposal in proposals:
        previous = decided.get(proposal.domain)
        if previous and previous[0] == "abgelehnt" and previous[1] == basis:
            skipped.append(proposal.domain)  # abgelehnt, Grundlage unverändert
            continue
        assessment = verify(ctx, facts, proposal)
        derived_only = {s["source"] for s in proposal.sources} == {"ableitung"}
        if derived_only and (
            not assessment.reachable or assessment.verdict in ("widerspricht", "unklar")
        ):
            continue  # Vermutung ohne eigenen Beleg auf der Seite wird nicht vorgeschlagen
        if assessment.domain != proposal.domain:
            later = decided.get(assessment.domain)
            if later and later[0] == "abgelehnt" and later[1] == basis:
                skipped.append(assessment.domain)
                continue
        _store(engine, company_id, proposal, assessment, basis, run_id)
        stored.append(assessment.domain)
    with engine.begin() as conn:
        conn.execute(
            insert(db.website_search).values(
                company_id=company_id,
                searched_at=db.utcnow(),
                search_basis=basis,
                outcome=json.dumps(
                    {"sources": outcome, "stored": stored, "skipped_rejected": skipped},
                    ensure_ascii=False,
                ),
                research_run_id=run_id,
            )
        )
    return {
        "company_id": company_id,
        "status": "gesucht",
        "stored": stored,
        "skipped_rejected": skipped,
        "sources": outcome,
    }


def _store(
    engine: Engine,
    company_id: int,
    proposal: Proposal,
    a: Assessment,
    basis: str,
    run_id: int | None,
) -> None:
    now = db.utcnow()
    sources = [*proposal.sources]
    with engine.begin() as conn:
        row = conn.execute(
            select(
                db.website_candidate.c.id,
                db.website_candidate.c.status,
                db.website_candidate.c.sources,
            ).where(
                db.website_candidate.c.company_id == company_id,
                db.website_candidate.c.domain == a.domain,
            )
        ).first()
        values = {
            "url": a.url,
            "score": a.score,
            "verdict": a.verdict,
            "checked_at": now,
            "details": json.dumps(a.details(), ensure_ascii=False),
            "document_ids": json.dumps(a.documents),
            "search_basis": basis,
        }
        if row is None:
            conn.execute(
                insert(db.website_candidate).values(
                    company_id=company_id,
                    domain=a.domain,
                    status="vorgeschlagen",
                    sources=json.dumps(sources, ensure_ascii=False),
                    research_run_id=run_id,
                    found_at=now,
                    **values,
                )
            )
        else:
            merged = json.loads(row.sources or "[]")
            for s in sources:
                if not any(m["source"] == s["source"] for m in merged):
                    merged.append(s)
            status = "vorgeschlagen" if row.status == "abgelehnt" else row.status  # neue Grundlage
            conn.execute(
                update(db.website_candidate)
                .where(db.website_candidate.c.id == row.id)
                .values(sources=json.dumps(merged, ensure_ascii=False), status=status, **values)
            )


# ---------------------------------------------------------------------- Entscheidungen


def candidates_of(engine: Engine, company_id: int) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(db.website_candidate)
                .where(db.website_candidate.c.company_id == company_id)
                .order_by(
                    db.website_candidate.c.status != "bestaetigt",
                    db.website_candidate.c.score.desc(),
                )
            )
            .mappings()
            .all()
        )
    return [_row(r) for r in rows]


def _row(r: Any) -> dict[str, Any]:
    out = dict(r)
    out["sources"] = json.loads(r["sources"] or "[]")
    out["details"] = json.loads(r["details"] or "{}")
    out["document_ids"] = json.loads(r["document_ids"] or "[]")
    out["verdict_label"] = VERDICT_LABELS.get(r["verdict"], r["verdict"])
    out["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
    return out


def last_search(engine: Engine, company_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(db.website_search)
                .where(db.website_search.c.company_id == company_id)
                .order_by(db.website_search.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return {**dict(row), "outcome": json.loads(row["outcome"] or "{}")}


def _author(author: str) -> str:
    author = author.strip()
    if not author:
        raise WebsiteError("Bitte einen Autor angeben (Nachvollziehbarkeit).")
    return author[:120]


def confirm(engine: Engine, candidate_id: int, author: str, reason: str = "") -> int:
    """Vorschlag bestätigen: wird die Website der Firma (Quelle „bestätigt“). Gibt Firma zurück."""
    from .maintenance import companies_with_domain

    author = _author(author)
    with engine.begin() as conn:
        cand = (
            conn.execute(
                select(db.website_candidate).where(db.website_candidate.c.id == candidate_id)
            )
            .mappings()
            .first()
        )
        if cand is None:
            raise WebsiteError("Vorschlag nicht gefunden")
        cid = int(cand["company_id"])
        others = companies_with_domain(conn, cand["domain"], exclude=cid)
        if others:
            raise WebsiteError(
                f"{cand['domain']} gehört bereits zu Firma {others[0]} – bitte unter Datenpflege prüfen (Dublette?)."
            )
        old = repo.company_row(conn, cid)["website_domain"]
        now = db.utcnow()
        conn.execute(
            update(db.company)
            .where(db.company.c.id == cid)
            .values(website_domain=cand["domain"], website_source="bestaetigt", updated_at=now)
        )
        repo.add_identifier(conn, cid, "domain", cand["domain"], None)
        conn.execute(
            update(db.website_candidate)
            .where(db.website_candidate.c.id == candidate_id)
            .values(
                status="bestaetigt", decided_at=now, decided_by=author, decision_reason=reason[:500]
            )
        )
        conn.execute(
            update(db.website_candidate)
            .where(
                db.website_candidate.c.company_id == cid,
                db.website_candidate.c.id != candidate_id,
                db.website_candidate.c.status.in_(["vorgeschlagen", "bestaetigt"]),
            )
            .values(status="nicht_gewaehlt", decided_at=now, decided_by=author)
        )
        conn.execute(
            insert(db.company_edit).values(
                company_id=cid,
                field="website_domain",
                old_value=old,
                new_value=cand["domain"],
                author=author,
                reason=reason
                or f"Website-Vorschlag bestätigt (Score {cand['score']}, {cand['verdict']})",
                created_at=now,
            )
        )
    return cid


def reject(engine: Engine, candidate_id: int, author: str, reason: str = "") -> int:
    """Vorschlag ablehnen; war er bestätigt, wird die Website der Firma entfernt."""
    author = _author(author)
    with engine.begin() as conn:
        cand = (
            conn.execute(
                select(db.website_candidate).where(db.website_candidate.c.id == candidate_id)
            )
            .mappings()
            .first()
        )
        if cand is None:
            raise WebsiteError("Vorschlag nicht gefunden")
        cid = int(cand["company_id"])
        now = db.utcnow()
        conn.execute(
            update(db.website_candidate)
            .where(db.website_candidate.c.id == candidate_id)
            .values(
                status="abgelehnt", decided_at=now, decided_by=author, decision_reason=reason[:500]
            )
        )
        row = repo.company_row(conn, cid)
        if row["website_domain"] == cand["domain"]:
            conn.execute(
                update(db.company)
                .where(db.company.c.id == cid)
                .values(website_domain=None, website_source=None, updated_at=now)
            )
            conn.execute(
                insert(db.company_edit).values(
                    company_id=cid,
                    field="website_domain",
                    old_value=cand["domain"],
                    new_value=None,
                    author=author,
                    reason=reason or "Website-Vorschlag abgelehnt",
                    created_at=now,
                )
            )
    return cid


def confirm_clear(engine: Engine, candidate_ids: list[int], author: str) -> list[int]:
    """Sammelaktion: nur Vorschläge mit Urteil „eindeutig“, je Firma nur der einzige eindeutige."""
    done: list[int] = []
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(db.website_candidate).where(db.website_candidate.c.id.in_(candidate_ids))
            )
            .mappings()
            .all()
        )
        clear_per_company: dict[int, list[Any]] = {}
        for r in conn.execute(
            select(db.website_candidate).where(
                db.website_candidate.c.company_id.in_({r["company_id"] for r in rows}),
                db.website_candidate.c.verdict == "eindeutig",
                db.website_candidate.c.status == "vorgeschlagen",
            )
        ).mappings():
            clear_per_company.setdefault(int(r["company_id"]), []).append(r)
    for r in rows:
        clear = clear_per_company.get(int(r["company_id"]), [])
        if r["status"] != "vorgeschlagen" or r["verdict"] != "eindeutig" or len(clear) != 1:
            continue  # mehrere eindeutige Vorschläge einer Firma: Mensch entscheidet
        confirm(engine, int(r["id"]), author, "Sammelbestätigung: eindeutige Zuordnung")
        done.append(int(r["company_id"]))
    return done


# ---------------------------------------------------------------------- Überblick


def website_state(engine: Engine, company_ids: list[int] | None = None) -> dict[int, str]:
    """Je Firma: bestaetigt | quelle | vorschlag | pruefen | keine."""
    with engine.connect() as conn:
        q = select(db.company.c.id, db.company.c.website_domain, db.company.c.website_source).where(
            db.company.c.merged_into_id.is_(None)
        )
        if company_ids is not None:
            q = q.where(db.company.c.id.in_(company_ids))
        companies = conn.execute(q).all()
        cands: dict[int, list[str]] = {}
        for cid, verdict in conn.execute(
            select(db.website_candidate.c.company_id, db.website_candidate.c.verdict).where(
                db.website_candidate.c.status == "vorgeschlagen"
            )
        ):
            cands.setdefault(int(cid), []).append(verdict)
    out: dict[int, str] = {}
    for cid, domain, source in companies:
        if domain and source in WEBSITE_SOURCE_LABELS:
            out[int(cid)] = "bestaetigt"
        elif cands.get(int(cid)):
            out[int(cid)] = "vorschlag" if "eindeutig" in cands[int(cid)] else "pruefen"
        elif domain:
            out[int(cid)] = "quelle"
        else:
            out[int(cid)] = "keine"
    return out


STATE_LABELS = {
    "bestaetigt": "Website bestätigt",
    "quelle": "Website aus Quelle übernommen, nicht bestätigt",
    "vorschlag": "eindeutiger Vorschlag wartet auf Bestätigung",
    "pruefen": "Vorschläge – manuelle Prüfung nötig",
    "keine": "keine Website bekannt",
}


def summary(states: dict[int, str]) -> dict[str, int]:
    counts = {k: 0 for k in STATE_LABELS}
    for state in states.values():
        counts[state] += 1
    return counts


def pending(engine: Engine, company_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Offene Vorschläge (Sammelansicht), beste je Firma zuerst."""
    with engine.connect() as conn:
        q = (
            select(db.website_candidate, db.company.c.display_name)
            .join(db.company, db.company.c.id == db.website_candidate.c.company_id)
            .where(db.website_candidate.c.status == "vorgeschlagen")
        )
        if company_ids is not None:
            q = q.where(db.website_candidate.c.company_id.in_(company_ids))
        rows = (
            conn.execute(q.order_by(db.company.c.display_name, db.website_candidate.c.score.desc()))
            .mappings()
            .all()
        )
    return [{**_row(r), "company_name": r["display_name"]} for r in rows]


def count_pending(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count(func.distinct(db.website_candidate.c.company_id))).where(
                    db.website_candidate.c.status == "vorgeschlagen"
                )
            ).scalar()
            or 0
        )
