"""Entity Resolution: Namensnormalisierung, Identifikator-Abgleich, Kandidaten statt Raten.

Regel (DATA_MODEL.md §7): Automatisch derselben Firma zugeordnet wird nur bei gleichem
starken Identifikator. Namensähnlichkeit erzeugt nur Kandidaten zur Prüfung.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

STRONG_SCHEMES = frozenset({"lei", "de_register", "vat", "sec_cik", "uk_crn", "nl_kvk", "wikidata"})

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "&": " und "})
# Reihenfolge: längere Formen zuerst, damit "gmbh & co. kg" vor "kg" greift.
_LEGAL_FORMS = (
    "gmbh und co kgaa",
    "gmbh und co kg",
    "ug haftungsbeschraenkt",
    "aktiengesellschaft",
    "gesellschaft mit beschraenkter haftung",
    "kommanditgesellschaft",
    "incorporated",
    "corporation",
    "limited",
    "company",
    "gmbh",
    "mbh",
    "kgaa",
    "ag",
    "se",
    "kg",
    "ohg",
    "ug",
    "ek",
    "ev",
    "eg",
    "plc",
    "ltd",
    "llc",
    "inc",
    "corp",
    "co",
    "sa",
    "sarl",
    "sas",
    "srl",
    "spa",
    "bv",
    "nv",
    "ab",
    "as",
    "oy",
    "spzoo",
    "sp z oo",
    "sro",
    "kft",
    "gesmbh",
)
_LEGAL_FORM_RE = re.compile(r"\b(" + "|".join(re.escape(f) for f in _LEGAL_FORMS) + r")\b")


def normalize_name(name: str) -> str:
    """Vergleichsform: Kleinbuchstaben, transliteriert, ohne Rechtsform und Satzzeichen."""
    text = unicodedata.normalize("NFKC", name).lower().translate(_TRANSLIT)
    text = re.sub(r"[.,;:/()\"'’`´\-+]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    previous = None
    while previous != text:  # Rechtsformen am Ende auch mehrfach entfernen
        previous = text
        text = _LEGAL_FORM_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text


def legal_form_of(name: str) -> str | None:
    """Erkannte Rechtsform (normalisiert) oder ``None``."""
    text = unicodedata.normalize("NFKC", name).lower().translate(_TRANSLIT)
    text = re.sub(r"[.,()]", " ", text)
    match = None
    for found in _LEGAL_FORM_RE.finditer(re.sub(r"\s+", " ", text)):
        match = found.group(1)
    return match


def name_similarity(a: str, b: str) -> float:
    """Ähnlichkeit 0..1 der normalisierten Namen (Zeichen- und Token-Vergleich)."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    token = len(ta & tb) / len(ta | tb)
    return round(max(ratio, token), 4)


def is_name_match(query: str, candidate: str, threshold: float = 0.92) -> bool:
    """Starke Namensübereinstimmung – reicht für einen Kandidaten, nie für ein Merge."""
    return name_similarity(query, candidate) >= threshold


def normalize_domain(url_or_domain: str) -> str | None:
    """``https://www.Example.com/x`` → ``example.com``."""
    text = url_or_domain.strip().lower()
    text = re.sub(r"^[a-z]+://", "", text).split("/")[0].split(":")[0]
    text = text.removeprefix("www.")
    return text if "." in text else None
