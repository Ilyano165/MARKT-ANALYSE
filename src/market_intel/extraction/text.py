"""HTML zu Text, Zitatprüfung und regelbasierte Extraktion (ohne KI).

Grundsatz: Eine Zahl wird nur gespeichert, wenn ein wörtliches Zitat aus dem gespeicherten
Dokument sie enthält. Das gilt für Regeln und für die optionale lokale KI gleichermaßen.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

EXTRACTOR_VERSION = "rules-0.1.0"

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "td",
    "th",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "address",
    "dd",
    "dt",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "form"}


class _TextParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []
        self.headings: list[str] = []
        self._heading: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("h1", "h2", "h3") and not self._skip:
            self._heading = []
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("mailto:", "tel:", "javascript:", "#")):
                self._href = urljoin(self.base_url, href)
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("h1", "h2", "h3") and self._heading is not None:
            heading = " ".join(" ".join(self._heading).split())
            if heading:
                self.headings.append(heading)
            self._heading = None
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._link_text).strip()))
            self._href = None
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self.parts.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._href is not None:
            self._link_text.append(data.strip())


@dataclass(frozen=True, slots=True)
class HtmlText:
    """Lesbarer Text einer Seite mit Titel und Links."""

    title: str
    text: str
    links: tuple[tuple[str, str], ...]
    headings: tuple[str, ...] = ()


def html_to_text(html: str, base_url: str) -> HtmlText:
    """Entfernt Skripte/Stile, behält Absatzstruktur und sammelt Links."""
    parser = _TextParser(base_url)
    parser.feed(html)
    parser.close()
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)
    return HtmlText(parser.title.strip(), text, tuple(parser.links), tuple(parser.headings))


def same_site_links(links: tuple[tuple[str, str], ...], base_url: str) -> list[tuple[str, str]]:
    """Nur Links auf derselben Domain (ohne www-Unterschied), https, ohne Dateien."""
    base_host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    result = []
    seen = set()
    for url, label in links:
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        if parts.scheme != "https" or host != base_host:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|svg|zip|mp4|webp|docx?|xlsx?)$", parts.path, re.I):
            continue
        clean = parts._replace(fragment="").geturl()
        if clean not in seen:
            seen.add(clean)
            result.append((clean, label))
    return result


# ---------------------------------------------------------------------- Zitatprüfung


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("­", "")
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """True, wenn das Zitat (whitespace-normalisiert) wörtlich im Text steht."""
    q = _norm(quote)
    return len(q) >= 3 and q in _norm(text)


def number_in_quote(value: Decimal, quote: str) -> bool:
    """True, wenn die Zahl im Zitat in einer üblichen Schreibweise vorkommt."""
    for found in re.findall(r"\d[\d.,'  ]*\d|\d", quote):
        parsed = parse_number(found)
        if parsed is not None and parsed == value:
            return True
    return False


def parse_number(raw: str) -> Decimal | None:
    """Deutsche und englische Zahlformate: 1.234 / 1,234 / 1.234,5 / 1 234."""
    s = raw.strip().replace("'", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = (
            s.replace(".", "").replace(",", ".")
            if s.rfind(",") > s.rfind(".")
            else s.replace(",", "")
        )
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = (
            s.replace(",", "")
            if len(tail) == 3 and head.replace(",", "").isdigit()
            else s.replace(",", ".")
        )
    elif "." in s:
        head, _, tail = s.rpartition(".")
        if len(tail) == 3 and s.count(".") >= 1 and head.replace(".", "").isdigit():
            s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------- Regeln


@dataclass(frozen=True, slots=True)
class EmployeeMention:
    """Mitarbeiterangabe in einem Text, mit wörtlichem Zitat."""

    value: Decimal
    quote: str
    approximate: bool


_EMPLOYEE_RE = re.compile(
    r"(?P<prefix>(?:über|mehr als|rund|ca\.?|etwa|knapp|more than|over|about|approximately|"
    r"around)\s+)?(?P<num>\d{1,3}(?:[.,' ]\d{3})+|\d{2,6})\s*\+?\s*"
    r"(?P<word>Mitarbeiterinnen und Mitarbeiter|Mitarbeitende[nr]?|Mitarbeiter(?:innen)?|"
    r"Beschäftigte[n]?|Angestellte[n]?|employees|staff members|people worldwide)",
    re.IGNORECASE,
)


def find_employee_mentions(text: str) -> list[EmployeeMention]:
    """Selbstangaben wie „rund 1.200 Mitarbeiter“; Zitat = umgebender Satzteil."""
    mentions = []
    for match in _EMPLOYEE_RE.finditer(text):
        value = parse_number(match.group("num"))
        if value is None or value < 2 or value > 2_000_000:
            continue
        start = max(0, text.rfind("\n", 0, match.start()) + 1, match.start() - 120)
        end = min(len(text), match.end() + 60)
        newline = text.find("\n", match.end())
        if newline != -1:
            end = min(end, newline)
        quote = text[start:end].strip()
        mentions.append(EmployeeMention(value, quote, bool(match.group("prefix"))))
    return mentions


@dataclass(frozen=True, slots=True)
class KeywordHit:
    """Treffer eines Anwendungsfall-Suchbegriffs mit Zitat."""

    term: str
    quote: str


def find_keyword_hits(text: str, terms: tuple[str, ...], max_hits: int = 3) -> list[KeywordHit]:
    """Ganzwort-/Wortanfangs-Treffer der Suchbegriffe, je Treffer ein kurzes Zitat."""
    hits: list[KeywordHit] = []
    lowered = text.lower()
    for term in terms:
        pattern = re.compile(r"(?<![\wäöüß])" + re.escape(term.lower()))
        match = pattern.search(lowered)
        if match is None:
            continue
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        quote = re.sub(r"\s+", " ", text[start:end]).strip()
        hits.append(KeywordHit(term, quote))
        if len(hits) >= max_hits:
            break
    return hits
