"""Strukturierte Inhalte aus Dokumenten: PDF-Text, Impressum, Stellenanzeigen (JSON-LD),
Pressemeldungen und Links auf Geschäftsberichte. Alles deterministisch und testbar.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

MAX_PDF_PAGES = 300
MAX_PDF_CHARS = 2_000_000


class PdfError(ValueError):
    """PDF nicht lesbar (beschädigt, verschlüsselt, zu groß)."""


def pdf_text(content: bytes, max_pages: int = MAX_PDF_PAGES) -> tuple[str, int]:
    """Text eines PDFs (pypdf, BSD-3). Gibt (Text, gelesene Seiten) zurück."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise PdfError("PDF ist verschlüsselt")
        parts: list[str] = []
        total = 0
        pages = 0
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ""
            total += len(text)
            pages += 1
            parts.append(text)
            if total > MAX_PDF_CHARS:
                break
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise PdfError(f"PDF nicht lesbar: {type(exc).__name__}: {exc}") from exc
    return "\n".join(parts), pages


# ---------------------------------------------------------------------- Impressum


@dataclass(frozen=True, slots=True)
class RegisterId:
    """Identifikator aus einem Impressum mit Zitat."""

    scheme: str  # de_register | vat
    value: str
    quote: str


_COURT = r"(?:Amtsgericht|AG|Registergericht)\s*:?\s*(?P<court>[A-ZÄÖÜ][\wäöüß.\- ]{1,40}?)"
_REGISTER_RE = re.compile(
    rf"(?:{_COURT}[,;:\s]+(?:Registernummer\s*:?\s*)?)?(?P<kind>HRB|HRA|GnR|PR|VR)\s*:?\s*"
    rf"(?P<num>\d{{1,7}})(?:\s*(?P<suffix>[A-Z]{{1,2}})\b)?",
)
_COURT_AFTER = re.compile(rf"\b(?P<kind>HRB|HRA)\s*(?P<num>\d{{1,7}})[,;\s]+{_COURT}")
_VAT_RE = re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3}|ATU\d{8}|CHE[-\s]?\d{3}\.\d{3}\.\d{3})\b")


def register_ids(text: str) -> list[RegisterId]:
    """Handelsregisternummer (mit Gericht) und USt-IdNr. aus Impressumstext."""
    found: list[RegisterId] = []
    for line in text.splitlines():
        if not re.search(r"HRB|HRA|USt|VAT|Umsatzsteuer|Registergericht|Amtsgericht", line, re.I):
            continue
        quote = re.sub(r"\s+", " ", line).strip()[:300]
        match = _REGISTER_RE.search(line) or _COURT_AFTER.search(line)
        if match and match.group("court"):
            court = re.sub(r"\s+", " ", match.group("court")).strip(" ,.;")
            court = re.sub(r"^(?:Amtsgericht|AG)\s+", "", court)
            suffix = match.groupdict().get("suffix") or ""
            value = f"{court} {match.group('kind')} {match.group('num')}"
            value += f" {suffix}" if suffix else ""
            found.append(RegisterId("de_register", value, quote))
        for vat in _VAT_RE.findall(line):
            found.append(RegisterId("vat", re.sub(r"[\s.\-]", "", vat), quote))
    unique: dict[tuple[str, str], RegisterId] = {}
    for item in found:
        unique.setdefault((item.scheme, item.value), item)
    return list(unique.values())


# ---------------------------------------------------------------------- Stellenanzeigen


@dataclass(frozen=True, slots=True)
class JobPosting:
    """Stellenanzeige aus schema.org-JobPosting (strukturierte Daten der Seite)."""

    title: str
    date_posted: date | None
    locality: str
    country: str


_LD_JSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _walk(node: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(node, list):
        for child in node:
            items += _walk(child)
    elif isinstance(node, dict):
        items.append(node)
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                items += _walk(node[key])
    return items


def job_postings(html: str) -> list[JobPosting]:
    """Alle JobPosting-Objekte aus JSON-LD-Blöcken; fehlerhafte Blöcke werden übersprungen."""
    postings = []
    for block in _LD_JSON.findall(html[:3_000_000]):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in _walk(data):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" not in kinds:
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            posted = None
            raw_date = str(item.get("datePosted") or "")[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
                try:
                    posted = date.fromisoformat(raw_date)
                except ValueError:
                    posted = None
            location = item.get("jobLocation") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address", {}) if isinstance(location, dict) else {}
            if not isinstance(address, dict):
                address = {}
            postings.append(
                JobPosting(
                    title=title[:200],
                    date_posted=posted,
                    locality=str(address.get("addressLocality") or "")[:100],
                    country=str(address.get("addressCountry") or "")[:40]
                    if not isinstance(address.get("addressCountry"), dict)
                    else str(address["addressCountry"].get("name", ""))[:40],
                )
            )
    return postings


# ---------------------------------------------------------------------- Pressemeldungen


@dataclass(frozen=True, slots=True)
class PressSignal:
    """Hinweis auf ein Ereignis in einer Unternehmensmeldung."""

    signal_type: str
    quote: str
    event_date: date | None


_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "new_location",
        r"neue[ns]? (?:standort|werk|niederlassung|produktionsstätte|lager)|"
        r"eröffne[tn]|opens? (?:a )?new (?:site|plant|facility|office)|new location",
    ),
    ("expansion", r"erweiter(?:t|ung)|ausbau|expan(?:sion|ds?)|vergrößer"),
    ("investment", r"investier(?:t|en)|investition|invest(?:s|ment)\b"),
    ("acquisition", r"übernimmt|übernahme|akquisition|acquir(?:es|ed|ition)|kauft"),
    ("partnership", r"partnerschaft|kooperation|partnership|joins forces"),
)
_DATE_DE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})")
_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _date_in(text: str) -> date | None:
    for regex, order in ((_DATE_ISO, (0, 1, 2)), (_DATE_DE, (2, 1, 0))):
        match = regex.search(text)
        if match:
            parts = [int(g) for g in match.groups()]
            try:
                return date(parts[order[0]], parts[order[1]], parts[order[2]])
            except ValueError:
                continue
    return None


def press_signals(text: str, page_date_hint: str = "") -> list[PressSignal]:
    """Ereignisse aus Presse-/Newsseiten. Datum nur, wenn es im Satz oder der Seite steht."""
    from .numbers import sentences

    signals = []
    fallback_date = _date_in(page_date_hint) if page_date_hint else None
    for sentence in sentences(text):
        if len(sentence) < 25:
            continue
        for kind, pattern in _SIGNAL_PATTERNS:
            if re.search(pattern, sentence, re.IGNORECASE):
                signals.append(
                    PressSignal(kind, sentence[:500], _date_in(sentence) or fallback_date)
                )
                break
    return signals[:20]


# ---------------------------------------------------------------------- Berichte


_REPORT_LINK = re.compile(
    r"gesch(?:ä|ae)ftsbericht|jahresbericht|jahresabschluss|konzernabschluss|annual[-_ ]?report|"
    r"investor|financial[-_ ]?(?:report|statements)|nachhaltigkeitsbericht|"
    r"sustainability[-_ ]report",
    re.IGNORECASE,
)


def report_links(links: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    """Links auf Geschäftsberichte/IR-Seiten (PDF zuerst)."""
    hits = [(url, label) for url, label in links if _REPORT_LINK.search(url + " " + label)]
    return sorted(hits, key=lambda h: 0 if h[0].lower().endswith(".pdf") else 1)
