"""FetchGateway: Freigabe, SSRF, robots.txt, TDM, Cache, Wiederholung, Protokoll."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from market_intel import db
from market_intel.domain import FetchStatus
from market_intel.http.gateway import FetchGateway

from .fakeweb import FakeWeb, public_resolver


def _gateway(engine, handler, sleeps, enabled=("website", "gleif", "gdelt")):
    from market_intel.repo import sync_sources

    sync_sources(engine, (), (), False)
    return FetchGateway(
        engine,
        user_agent="Test/1.0",
        enabled_sources=frozenset(enabled),
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        sleep=sleeps.append,
    )


def _log(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(db.fetch_log)).mappings()]


def test_blocked_source_makes_no_network_call(engine, sleeps):
    calls = []
    gw = _gateway(engine, lambda r: calls.append(r) or httpx.Response(200), sleeps)
    for key in ("northdata", "handelsregister", "ba_jobsuche", "sec_edgar"):
        result = gw.get(key, "https://example.test/", purpose="test")
        assert result.status is FetchStatus.BLOCKED_POLICY
    assert calls == []
    assert [r["status"] for r in _log(engine)] == ["blocked_policy"] * 4


def test_ssrf_protection(engine, sleeps):
    calls = []
    gw = _gateway(engine, lambda r: calls.append(r) or httpx.Response(200), sleeps)
    for url in (
        "http://example.test/",
        "https://127.0.0.1/",
        "https://10.1.2.3/",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/",
        "https://intranet.local/",
        "https://example.test:8443/",
        "https://user:pw@example.test/",
    ):
        assert gw.get("website", url, purpose="t").status is FetchStatus.BLOCKED_SSRF, url
    private = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"website"}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        resolver=lambda host: ["192.168.0.10"],
        sleep=sleeps.append,
    )
    assert private.get("website", "https://rebind.test/", purpose="t").status is (
        FetchStatus.BLOCKED_SSRF
    )
    assert calls == []


def test_robots_and_tdm_reservation_are_respected(engine, sleeps):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privat\n")
        if request.url.path == "/tdm":
            return httpx.Response(
                200,
                text='<html><head><meta name="tdm-reservation" content="1"></head></html>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    gw = _gateway(engine, handler, sleeps)
    blocked = gw.get("website", "https://site.test/privat/x", purpose="t", respect_robots=True)
    assert blocked.status is FetchStatus.BLOCKED_ROBOTS
    tdm = gw.get("website", "https://site.test/tdm", purpose="t", respect_robots=True)
    assert tdm.status is FetchStatus.BLOCKED_TDM
    assert tdm.document_id is None  # nichts gespeichert
    ok = gw.get("website", "https://site.test/ok", purpose="t", respect_robots=True)
    assert ok.ok and ok.document_id is not None


def test_cache_prevents_duplicate_requests(engine, sleeps):
    web = FakeWeb()
    gw = _gateway(engine, web.handle, sleeps)
    first = gw.get(
        "gleif",
        "https://api.gleif.org/api/v1/lei-records",
        purpose="t",
        params={"filter[fulltext]": "3M"},
    )
    second = gw.get(
        "gleif",
        "https://api.gleif.org/api/v1/lei-records",
        purpose="t",
        params={"filter[fulltext]": "3M"},
    )
    assert first.status is FetchStatus.OK and second.status is FetchStatus.CACHED
    assert second.content == first.content
    assert web.calls["api.gleif.org"] == 1


def test_retry_after_is_honoured_and_failures_logged(engine, sleeps):
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(503),
            httpx.Response(200, json={"data": []}),
        ]
    )
    gw = _gateway(engine, lambda r: next(responses), sleeps)
    result = gw.get("gleif", "https://api.gleif.org/api/v1/x", purpose="t")
    assert result.ok
    assert 7.0 in sleeps
    assert [r["status"] for r in _log(engine)] == ["http_error", "http_error", "ok"]


def test_network_error_is_reported_not_hidden(engine, sleeps):
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    gw = _gateway(engine, handler, sleeps)
    result = gw.get("gleif", "https://api.gleif.org/api/v1/x", purpose="t")
    assert result.status is FetchStatus.NETWORK_ERROR and not result.ok
    assert len(_log(engine)) == 3  # drei Versuche, jeder protokolliert


def test_response_size_limit(engine, sleeps):
    gw = _gateway(engine, lambda r: httpx.Response(200, content=b"x" * 2000), sleeps)
    result = gw.get("gleif", "https://api.gleif.org/big", purpose="t", max_bytes=1000)
    assert result.status is FetchStatus.TOO_LARGE


def test_redirect_is_not_followed_blindly(engine, sleeps):
    gw = _gateway(
        engine,
        lambda r: httpx.Response(301, headers={"location": "https://127.0.0.1/admin"}),
        sleeps,
    )
    result = gw.get("website", "https://site.test/", purpose="t")
    assert not result.ok and result.location == "https://127.0.0.1/admin"


def test_request_limit_per_run(engine, sleeps):
    from market_intel.repo import sync_sources

    sync_sources(engine, (), (), False)
    gw = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"gleif"}),
        max_requests=2,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        resolver=public_resolver,
        sleep=sleeps.append,
    )
    statuses = [gw.get("gleif", f"https://api.gleif.org/{i}", purpose="t").status for i in range(3)]
    assert statuses[-1] is FetchStatus.BLOCKED_POLICY


def test_configurable_rate_limit_is_respected(engine, sleeps):
    from market_intel.repo import sync_sources

    sync_sources(engine, (), (), False)
    clock = [0.0]
    gw = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"gleif"}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        resolver=public_resolver,
        sleep=lambda s: sleeps.append(s),
        monotonic=lambda: clock[0],
        min_intervals={"gleif": 4.0},
    )
    gw.get("gleif", "https://api.gleif.org/a", purpose="t")
    gw.get("gleif", "https://api.gleif.org/b", purpose="t")
    assert sleeps and sleeps[-1] == 4.0


def test_cost_budget_blocks_paid_source_before_request(engine, sleeps, monkeypatch):
    import dataclasses

    from market_intel.repo import sync_sources
    from market_intel.sources import registry

    paid = dataclasses.replace(registry.SOURCE_BY_KEY["gleif"], cost_per_call_eur=0.05)
    monkeypatch.setitem(registry.SOURCE_BY_KEY, "gleif", paid)
    sync_sources(engine, (), (), False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    gw = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"gleif"}),
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        sleep=sleeps.append,
        cost_limit_eur=0.10,
    )
    statuses = [gw.get("gleif", f"https://api.gleif.org/{i}", purpose="t").status for i in range(3)]
    assert statuses[:2] == [FetchStatus.OK, FetchStatus.OK]
    assert statuses[2] is FetchStatus.BLOCKED_POLICY and len(calls) == 2
    default = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"gleif"}),
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        sleep=sleeps.append,
    )
    assert default.get("gleif", "https://api.gleif.org/x", purpose="t").status is (
        FetchStatus.BLOCKED_POLICY
    )  # Standardbudget 0 € → keine Bezahlabrufe


def test_timeouts_count_as_network_errors_with_limited_retries(engine, sleeps):
    def handler(request):
        raise httpx.ReadTimeout("zu langsam", request=request)

    from market_intel.repo import sync_sources

    sync_sources(engine, (), (), False)
    gw = FetchGateway(
        engine,
        user_agent="T",
        enabled_sources=frozenset({"gleif"}),
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        sleep=sleeps.append,
        max_attempts=2,
    )
    assert gw.get("gleif", "https://api.gleif.org/x", purpose="t").status is (
        FetchStatus.NETWORK_ERROR
    )
    assert len(_log(engine)) == 2
