"""Einziger Weg ins Internet: Freigabe, SSRF-Schutz, robots.txt, TDM-Vorbehalt, Rate Limit,
Cache, Wiederholung, Größenlimit und Protokoll jedes Aufrufs (ARCHITECTURE.md ADR-06).

Jeder Aufruf – auch ein gescheiterter – erzeugt eine Zeile in ``fetch_log``. Erfolgreiche
Antworten werden als ``source_document`` mit SHA-256 gespeichert.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import time
import urllib.robotparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import Engine, insert, select

from .. import db
from ..domain import FetchStatus
from ..sources.registry import SOURCE_BY_KEY

MAX_BYTES_DEFAULT = 5 * 1024 * 1024
# Live-Test 2026-10-23: GDELT drosselte jede Anfrage (geteilte Ausgangs-IP); weitere Versuche
# belasten nur die Quelle und kosten Minuten. Nach so vielen gescheiterten Abrufen in Folge Pause.
RATE_LIMIT_SUSPEND_AFTER = 2
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 60.0
_TDM_META = re.compile(
    r"<meta[^>]+name=[\"']tdm-reservation[\"'][^>]+content=[\"']1[\"']", re.IGNORECASE
)

Resolver = Callable[[str], list[str]]


def _system_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _proxy_configured() -> bool:
    return any(os.environ.get(name) for name in ("HTTPS_PROXY", "https_proxy"))


class PolicyBlocked(Exception):
    """Abruf aus Rechts-, Sicherheits- oder Budgetgründen nicht erlaubt."""

    def __init__(self, status: FetchStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(slots=True)
class FetchResult:
    """Ergebnis eines Abrufs."""

    status: FetchStatus
    url: str
    http_status: int | None = None
    document_id: int | None = None
    content: bytes = b""
    content_type: str = ""
    error: str = ""
    location: str = ""

    @property
    def ok(self) -> bool:
        """True, wenn Inhalt vorliegt."""
        return self.status.succeeded

    @property
    def text(self) -> str:
        """Inhalt als Text (UTF-8, fehlertolerant)."""
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Inhalt als JSON."""
        return json.loads(self.content)


@dataclass(slots=True)
class GatewayStats:
    """Zähler je Run für Berichte."""

    by_status: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, dict[str, int]] = field(default_factory=dict)
    requests: int = 0
    cost_eur: float = 0.0

    def count(self, source_key: str, status: FetchStatus) -> None:
        self.by_status[status.value] = self.by_status.get(status.value, 0) + 1
        per = self.by_source.setdefault(source_key, {})
        per[status.value] = per.get(status.value, 0) + 1


class FetchGateway:
    """Zentraler HTTP-Zugang. Adapter dürfen keine eigenen Verbindungen öffnen."""

    def __init__(
        self,
        engine: Engine,
        *,
        user_agent: str,
        enabled_sources: frozenset[str],
        max_requests: int = 2000,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        timeout: float = 30.0,
        max_attempts: int = MAX_ATTEMPTS,
        min_intervals: Mapping[str, float] | None = None,
        cost_limit_eur: float = 0.0,
    ) -> None:
        self._engine = engine
        self._max_attempts = max(1, max_attempts)
        self._min_intervals = dict(min_intervals or {})
        self._cost_limit = cost_limit_eur
        self._enabled = enabled_sources
        self._max_requests = max_requests
        self._resolver = resolver or _system_resolver
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: dict[str, float] = {}
        # Host → Grund: Host hat den Zugriff verweigert (401/403, Bot-Abwehr, Proxy-Sperre).
        # Weitere Abrufe in dieser Sitzung unterbleiben – Sperren werden respektiert.
        self._denied_hosts: dict[str, str] = {}
        self._throttled: dict[str, int] = {}  # Host → Abrufe in Folge, die an 429 scheiterten
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.user_agent = user_agent
        self.run_id: int | None = None
        self.requests_offset = 0  # bereits in früheren Sitzungen des Auftrags verbrauchte Abrufe
        self.stats = GatewayStats()
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    @property
    def max_requests(self) -> int:
        """Abruflimit für die laufende Sitzung (Gesamtlimit minus frühere Sitzungen)."""
        return max(0, self._max_requests - self.requests_offset)

    @property
    def enabled_sources(self) -> frozenset[str]:
        """Aktivierte Quellen."""
        return self._enabled

    def close(self) -> None:
        """Schließt die Verbindungen."""
        self._client.close()

    # ------------------------------------------------------------------ öffentliche API

    def get(
        self,
        source_key: str,
        url: str,
        *,
        purpose: str,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        ttl: timedelta = timedelta(days=1),
        respect_robots: bool = False,
        max_bytes: int = MAX_BYTES_DEFAULT,
        title: str | None = None,
    ) -> FetchResult:
        """GET mit allen Schutzmechanismen."""
        full_url = f"{url}?{urlencode(sorted(params.items()))}" if params else url
        return self._fetch(
            source_key,
            "GET",
            full_url,
            purpose=purpose,
            body=None,
            headers=headers,
            ttl=ttl,
            respect_robots=respect_robots,
            max_bytes=max_bytes,
            title=title,
        )

    def post_json(
        self,
        source_key: str,
        url: str,
        payload: Mapping[str, Any],
        *,
        purpose: str,
        ttl: timedelta = timedelta(hours=12),
        title: str | None = None,
    ) -> FetchResult:
        """POST mit JSON-Body (z. B. TED-Suche); Cache-Schlüssel enthält den Body."""
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return self._fetch(
            source_key,
            "POST",
            url,
            purpose=purpose,
            body=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            ttl=ttl,
            respect_robots=False,
            max_bytes=MAX_BYTES_DEFAULT,
            title=title,
        )

    # ------------------------------------------------------------------ Ablauf

    def _fetch(
        self,
        source_key: str,
        method: str,
        url: str,
        *,
        purpose: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        ttl: timedelta,
        respect_robots: bool,
        max_bytes: int,
        title: str | None,
    ) -> FetchResult:
        body_hash = hashlib.sha256(body or b"").hexdigest()
        cache_key = hashlib.sha256(f"{method} {url} {body_hash}".encode()).hexdigest()
        try:
            self._check_policy(source_key)
            self._check_url(url)
            denied = self._denied_hosts.get(urlsplit(url).hostname or "")
            if denied:
                raise PolicyBlocked(
                    FetchStatus.BLOCKED_POLICY,
                    f"{denied} – keine weiteren Abrufe dieses Hosts in diesem Lauf",
                )
            if respect_robots:
                self._check_robots(source_key, url)
        except PolicyBlocked as blocked:
            return self._log(
                source_key, method, url, purpose, blocked.status, None, blocked.reason, 1, 0, None
            )

        cached = self._cache_lookup(cache_key)
        if cached is not None and cached["fresh"]:
            result = self._log(
                source_key,
                method,
                url,
                purpose,
                FetchStatus.CACHED,
                cached["http_status"],
                "",
                1,
                0,
                cached["document_id"],
            )
            result.content = cached["body"]
            result.content_type = cached["content_type"] or ""
            return result

        request_headers = dict(headers or {})
        if cached is not None:
            if cached["etag"]:
                request_headers["If-None-Match"] = cached["etag"]
            if cached["last_modified"]:
                request_headers["If-Modified-Since"] = cached["last_modified"]

        last_error = ""
        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            if self.stats.requests + self.requests_offset >= self._max_requests:
                return self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.BLOCKED_POLICY,
                    None,
                    f"Anfragelimit des Runs ({self._max_requests}) erreicht",
                    attempt,
                    0,
                    None,
                )
            cost = SOURCE_BY_KEY[source_key].cost_per_call_eur
            if cost > 0 and self.stats.cost_eur + cost > self._cost_limit:
                return self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.BLOCKED_POLICY,
                    None,
                    f"Kostenbudget des Auftrags ({self._cost_limit:.2f} €) würde überschritten",
                    attempt,
                    0,
                    None,
                )
            self._throttle(source_key, url)
            self.stats.requests += 1
            self.stats.cost_eur += cost
            started = self._monotonic()
            try:
                response, content = self._send(method, url, body, request_headers, max_bytes)
            except _TooLarge as exc:
                return self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.TOO_LARGE,
                    None,
                    str(exc),
                    attempt,
                    self._elapsed(started),
                    None,
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Proxy/Firewall verweigert die Verbindung grundsätzlich (z. B. CONNECT 403/407):
                # Wiederholen ändert nichts, kostet nur Zeit und Abrufbudget.
                policy_block = isinstance(exc, httpx.ProxyError) and any(
                    code in str(exc) for code in ("403", "407")
                )
                if policy_block:
                    last_error += " (Netzwerkrichtlinie/Proxy sperrt den Host – keine Wiederholung)"
                    self._deny(url, f"Proxy/Netzwerkrichtlinie sperrt den Host ({exc})")
                self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.NETWORK_ERROR,
                    None,
                    last_error,
                    attempt,
                    self._elapsed(started),
                    None,
                )
                if attempt < self._max_attempts and not policy_block:
                    self._sleep(2.0 ** (attempt - 1))
                    continue
                return FetchResult(FetchStatus.NETWORK_ERROR, url, error=last_error)

            duration = self._elapsed(started)
            last_status = response.status_code
            if response.status_code == 304 and cached is not None:
                self._cache_store(cache_key, cached["document_id"], response, ttl)
                result = self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.NOT_MODIFIED,
                    304,
                    "",
                    attempt,
                    duration,
                    cached["document_id"],
                )
                result.content = cached["body"]
                result.content_type = cached["content_type"] or ""
                return result
            if 300 <= response.status_code < 400:
                location = response.headers.get("location", "")
                redirect = self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.HTTP_ERROR,
                    response.status_code,
                    f"Weiterleitung nach {location or '?'} wird nicht "
                    "automatisch verfolgt (Ziel wird erneut geprüft)",
                    attempt,
                    duration,
                    None,
                )
                redirect.location = location
                return redirect
            if response.status_code >= 400:
                last_error = (
                    f"HTTP {response.status_code}: {content[:300].decode(errors='replace')}"
                )
                self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.HTTP_ERROR,
                    response.status_code,
                    last_error,
                    attempt,
                    duration,
                    None,
                )
                if response.status_code in (401, 403):
                    challenge = response.headers.get("cf-mitigated", "") == "challenge"
                    self._deny(
                        url,
                        f"Host verweigerte den Zugriff (HTTP {response.status_code}"
                        + (", Bot-Abwehr/Challenge" if challenge else "")
                        + ")",
                    )
                if response.status_code in RETRY_STATUSES and attempt < self._max_attempts:
                    self._sleep(self._retry_delay(response, attempt))
                    continue
                if response.status_code == 429:
                    self._rate_limited(url)
                return FetchResult(
                    FetchStatus.HTTP_ERROR, url, response.status_code, error=last_error
                )

            content_type = response.headers.get("content-type", "")
            if self._tdm_reserved(source_key, response, content):
                return self._log(
                    source_key,
                    method,
                    url,
                    purpose,
                    FetchStatus.BLOCKED_TDM,
                    response.status_code,
                    "Nutzungsvorbehalt für Text und Data Mining (§ 44b UrhG)",
                    attempt,
                    duration,
                    None,
                )
            self._throttled.pop(urlsplit(url).hostname or "", None)
            document_id = self._store_document(source_key, url, body_hash, response, content, title)
            self._cache_store(cache_key, document_id, response, ttl)
            result = self._log(
                source_key,
                method,
                url,
                purpose,
                FetchStatus.OK,
                response.status_code,
                "",
                attempt,
                duration,
                document_id,
            )
            result.content = content
            result.content_type = content_type
            return result
        return FetchResult(FetchStatus.HTTP_ERROR, url, last_status, error=last_error)

    # ------------------------------------------------------------------ Prüfungen

    def _check_policy(self, source_key: str) -> None:
        definition = SOURCE_BY_KEY.get(source_key)
        if definition is None:
            raise PolicyBlocked(FetchStatus.BLOCKED_POLICY, f"Unbekannte Quelle {source_key}")
        if not definition.fetchable:
            raise PolicyBlocked(
                FetchStatus.BLOCKED_POLICY,
                f"Quelle {source_key} ist nicht freigegeben ({definition.legal_status})",
            )
        if source_key not in self._enabled:
            raise PolicyBlocked(FetchStatus.BLOCKED_POLICY, f"Quelle {source_key} ist deaktiviert")

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise PolicyBlocked(FetchStatus.BLOCKED_SSRF, "Nur https-Adressen sind erlaubt")
        host = parts.hostname or ""
        if not host or parts.username or parts.password:
            raise PolicyBlocked(FetchStatus.BLOCKED_SSRF, "Ungültiger Host")
        if parts.port not in (None, 443):
            raise PolicyBlocked(FetchStatus.BLOCKED_SSRF, f"Port {parts.port} nicht erlaubt")
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            if host.endswith((".local", ".internal", ".localhost")) or host == "localhost":
                raise PolicyBlocked(FetchStatus.BLOCKED_SSRF, "Interner Hostname") from None
            try:
                addresses = self._resolver(host)
            except OSError:
                # Hinter einem HTTPS-Proxy löst der Proxy auf; private Netze sind dort gesperrt.
                if _proxy_configured():
                    return
                raise PolicyBlocked(
                    FetchStatus.BLOCKED_SSRF, f"Host {host} nicht auflösbar"
                ) from None
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise PolicyBlocked(
                    FetchStatus.BLOCKED_SSRF, f"{host} zeigt auf nicht-öffentliche Adresse {ip}"
                )

    def _check_robots(self, source_key: str, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(source_key, origin)
        parser = self._robots[origin]
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            raise PolicyBlocked(FetchStatus.BLOCKED_ROBOTS, f"robots.txt verbietet {parts.path}")

    def _load_robots(
        self, source_key: str, origin: str
    ) -> urllib.robotparser.RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        result = self._fetch(
            source_key,
            "GET",
            robots_url,
            purpose="robots.txt",
            body=None,
            headers=None,
            ttl=timedelta(hours=24),
            respect_robots=False,
            max_bytes=512 * 1024,
            title="robots.txt",
        )
        parser = urllib.robotparser.RobotFileParser()
        if result.ok:
            parser.parse(result.text.splitlines())
            return parser
        if result.http_status in (401, 403):
            parser.parse(["User-agent: *", "Disallow: /"])  # Zugriff verweigert = alles gesperrt
            return parser
        return None  # 404 oder Fehler: keine Einschränkung bekannt (RFC 9309)

    def _tdm_reserved(self, source_key: str, response: httpx.Response, content: bytes) -> bool:
        if SOURCE_BY_KEY[source_key].source_type != "company_website":
            return False
        if response.headers.get("tdm-reservation", "").strip() == "1":
            return True
        if "html" in response.headers.get("content-type", ""):
            return bool(_TDM_META.search(content[:200_000].decode("utf-8", errors="replace")))
        return False

    # ------------------------------------------------------------------ Hilfen

    def _deny(self, url: str, reason: str) -> None:
        host = urlsplit(url).hostname or ""
        self._denied_hosts.setdefault(host, reason)

    def _rate_limited(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        self._throttled[host] = self._throttled.get(host, 0) + 1
        if self._throttled[host] >= RATE_LIMIT_SUSPEND_AFTER:
            self._deny(
                url,
                f"Host drosselt dauerhaft (HTTP 429 auch nach allen Wiederholungen, "
                f"{self._throttled[host]}× in Folge) – Abrufe ausgesetzt",
            )

    def _throttle(self, source_key: str, url: str) -> None:
        interval = max(
            SOURCE_BY_KEY[source_key].min_interval_seconds,
            self._min_intervals.get(source_key, 0.0),
        )
        host_key = f"{source_key}:{urlsplit(url).hostname}"
        last = self._last_request.get(host_key)
        now = self._monotonic()
        if last is not None and now - last < interval:
            self._sleep(interval - (now - last))
        self._last_request[host_key] = self._monotonic()

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        return float(2**attempt)

    def _send(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str], max_bytes: int
    ) -> tuple[httpx.Response, bytes]:
        with self._client.stream(method, url, content=body, headers=headers) as response:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise _TooLarge(f"Antwort größer als {max_bytes} Bytes")
                chunks.append(chunk)
            return response, b"".join(chunks)

    def _elapsed(self, started: float) -> int:
        return int((self._monotonic() - started) * 1000)

    def _store_document(
        self,
        source_key: str,
        url: str,
        body_hash: str,
        response: httpx.Response,
        content: bytes,
        title: str | None,
    ) -> int:
        digest = hashlib.sha256(content).hexdigest()
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(db.source_document.c.id).where(
                    db.source_document.c.source_key == source_key,
                    db.source_document.c.url == url,
                    db.source_document.c.request_body_sha256 == body_hash,
                    db.source_document.c.sha256 == digest,
                )
            ).scalar()
            if existing is not None:
                return int(existing)
            return int(
                conn.execute(
                    insert(db.source_document).values(
                        source_key=source_key,
                        url=url,
                        final_url=str(response.url),
                        request_body_sha256=body_hash,
                        title=title,
                        publisher=SOURCE_BY_KEY[source_key].publisher,
                        retrieved_at=db.utcnow(),
                        http_status=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        sha256=digest,
                        body=content,
                        run_id=self.run_id,
                    )
                ).inserted_primary_key[0]
            )

    def _cache_lookup(self, cache_key: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(
                        db.http_cache,
                        db.source_document.c.body,
                        db.source_document.c.http_status,
                        db.source_document.c.content_type,
                    )
                    .join(
                        db.source_document, db.source_document.c.id == db.http_cache.c.document_id
                    )
                    .where(db.http_cache.c.cache_key == cache_key)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        expires = row["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=db.utcnow().tzinfo)
        return {**row, "fresh": expires > db.utcnow()}

    def _cache_store(
        self, cache_key: str, document_id: int, response: httpx.Response, ttl: timedelta
    ) -> None:
        now = db.utcnow()
        values = {
            "document_id": document_id,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "fetched_at": now,
            "expires_at": now + ttl,
        }
        with self._engine.begin() as conn:
            updated = conn.execute(
                db.http_cache.update()
                .where(db.http_cache.c.cache_key == cache_key)
                .values(**values)
            ).rowcount
            if not updated:
                conn.execute(insert(db.http_cache).values(cache_key=cache_key, **values))

    def _log(
        self,
        source_key: str,
        method: str,
        url: str,
        purpose: str,
        status: FetchStatus,
        http_status: int | None,
        error: str,
        attempt: int,
        duration_ms: int,
        document_id: int | None,
    ) -> FetchResult:
        self.stats.count(source_key, status)
        with self._engine.begin() as conn:
            conn.execute(
                insert(db.fetch_log).values(
                    run_id=self.run_id,
                    source_key=source_key,
                    method=method,
                    url=url[:2000],
                    purpose=purpose,
                    status=status.value,
                    http_status=http_status,
                    error=error[:1000] or None,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    document_id=document_id,
                    at=db.utcnow(),
                )
            )
        return FetchResult(status, url, http_status, document_id, error=error)


class _TooLarge(Exception):
    pass
