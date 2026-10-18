"""Synthetisches Test-Internet (httpx.MockTransport).

ACHTUNG: Alle Antworten sind erfundene TESTDATEN im dokumentierten Format der jeweiligen
Quelle. Sie dienen nur dazu, Verarbeitung, Fehlerbehandlung und Nachvollziehbarkeit zu prüfen.
Sie sind kein Nachweis eines echten Recherchelaufs (dafür: ``mi prove``).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx

PUBLIC_IP = "93.184.216.34"


def public_resolver(host: str) -> list[str]:
    """Löst jede Testdomain auf eine öffentliche Adresse auf."""
    return [PUBLIC_IP]


def _lei_record(lei: str, name: str, country: str, city: str) -> dict[str, Any]:
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name, "language": "de"},
                "otherNames": [],
                "legalAddress": {
                    "addressLines": ["Teststraße 1"],
                    "city": city,
                    "country": country,
                    "postalCode": "12345",
                },
                "headquartersAddress": {"city": city, "country": country},
                "registeredAt": {"id": "RA000000"},
                "registeredAs": "HRB 0000",
                "status": "ACTIVE",
            },
        },
    }


def _claim_quantity(amount: str, year: int, unit: str = "1") -> dict[str, Any]:
    return {
        "rank": "normal",
        "mainsnak": {"datavalue": {"type": "quantity", "value": {"amount": amount, "unit": unit}}},
        "qualifiers": {
            "P585": [{"datavalue": {"value": {"time": f"+{year}-00-00T00:00:00Z", "precision": 9}}}]
        },
        "references": [
            {"snaks": {"P854": [{"datavalue": {"value": "https://example.test/annual-report"}}]}}
        ],
    }


def _claim_string(value: str) -> dict[str, Any]:
    return {"rank": "normal", "mainsnak": {"datavalue": {"type": "string", "value": value}}}


def _claim_item(qid: str) -> dict[str, Any]:
    return {"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": qid}}}}


USD_UNIT = "http://www.wikidata.org/entity/Q900901"

ENTITIES: dict[str, dict[str, Any]] = {
    "Q900001": {
        "id": "Q900001",
        "labels": {"de": {"value": "3M"}},
        "claims": {
            "P1278": [_claim_string("TESTLEI3M0000000001")],
            "P5531": [_claim_string("0000999001")],
            "P856": [_claim_string("https://www.alpha-films.test/")],
            "P17": [_claim_item("Q900900")],
            "P452": [_claim_item("Q900950")],
            "P1128": [_claim_quantity("+90000", 2021), _claim_quantity("+85000", 2023)],
            "P2139": [_claim_quantity("+35000000000", 2023, USD_UNIT)],
        },
    },
    "Q900002": {
        "id": "Q900002",
        "labels": {"de": {"value": "Avery Dennison"}},
        "claims": {
            "P1278": [_claim_string("TESTLEIAVERY00000001")],
            "P5531": [_claim_string("0000999002")],
            "P17": [_claim_item("Q900900")],
            "P1128": [_claim_quantity("+36000", 2021), _claim_quantity("+35000", 2023)],
        },
    },
    "Q900003": {
        "id": "Q900003",
        "labels": {"de": {"value": "ORAFOL"}},
        "claims": {
            "P1278": [_claim_string("TESTLEIORAFOL0000001")],
            "P17": [_claim_item("Q900902")],
            "P1128": [_claim_quantity("+2400", 2020), _claim_quantity("+2900", 2023)],
        },
    },
    "Q900900": {
        "id": "Q900900",
        "labels": {"de": {"value": "Testland US"}},
        "claims": {"P297": [_claim_string("US")]},
    },
    "Q900902": {
        "id": "Q900902",
        "labels": {"de": {"value": "Testland DE"}},
        "claims": {"P297": [_claim_string("DE")]},
    },
    "Q900901": {
        "id": "Q900901",
        "labels": {"de": {"value": "Testdollar"}},
        "claims": {"P498": [_claim_string("USD")]},
    },
    "Q900950": {"id": "Q900950", "labels": {"de": {"value": "Werbetechnik"}}, "claims": {}},
    "Q900010": {
        "id": "Q900010",
        "labels": {"de": {"value": "Beta Werbetechnik"}},
        "claims": {
            "P17": [_claim_item("Q900902")],
            "P452": [_claim_item("Q900950")],
            "P856": [_claim_string("https://beta-werbung.test/")],
        },
    },
}

SEARCH = {
    "3M": "Q900001",
    "Avery Dennison": "Q900002",
    "ORAFOL": "Q900003",
    "Werbetechnik": "Q900950",
}

GLEIF = {
    "3M": [_lei_record("TESTLEI3M0000000001", "3M Company", "US", "Saint Paul")],
    "Avery Dennison": [
        _lei_record("TESTLEIAVERY00000001", "Avery Dennison Corporation", "US", "Mentor")
    ],
    "ORAFOL": [_lei_record("TESTLEIORAFOL0000001", "ORAFOL Europe GmbH", "DE", "Oranienburg")],
    "Fahrzeugfolierung": [
        _lei_record("TESTLEIGAMMA00000001", "Gamma Fahrzeugfolierung GmbH", "DE", "Köln")
    ],
}


def _facts(cik: int, revenues: list[tuple[str, str, int, str]]) -> dict[str, Any]:
    return {
        "cik": cik,
        "entityName": "Test",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": start,
                                "end": end,
                                "val": val,
                                "accn": f"0000-{end}",
                                "fy": int(end[:4]),
                                "fp": "FY",
                                "form": "10-K",
                                "filed": filed,
                            }
                            for start, end, val, filed in revenues
                        ]
                    }
                }
            }
        },
    }


SEC = {
    999001: _facts(
        999001,
        [
            ("2021-01-01", "2021-12-31", 35355000000, "2022-02-09"),
            ("2022-01-01", "2022-12-31", 34229000000, "2023-02-08"),
            ("2023-01-01", "2023-12-31", 32681000000, "2024-02-07"),
        ],
    ),
    999002: _facts(
        999002,
        [
            ("2022-01-02", "2022-12-31", 9039300000, "2023-02-22"),
            ("2023-01-01", "2023-12-30", 8364300000, "2024-02-21"),
        ],
    ),
}

ECB_CSV = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
    "EXR.A.USD.EUR.SP00.A,A,USD,EUR,SP00,A,2021,1.1827\n"
    "EXR.A.USD.EUR.SP00.A,A,USD,EUR,SP00,A,2022,1.0530\n"
    "EXR.A.USD.EUR.SP00.A,A,USD,EUR,SP00,A,2023,1.0813\n"
)

PAGES = {
    "https://www.alpha-films.test/": "<html><head><title>Alpha</title></head><body>"
    "<p>Folien für Car Wrapping und Fahrzeugfolierung.</p></body></html>",
    "https://beta-werbung.test/": "<html><head><title>Beta Werbetechnik</title></head><body>"
    "<h1>Beta Werbetechnik</h1><p>Wir übernehmen Fahrzeugfolierung, Vollfolierung und "
    "Flottenbeschriftung für Handwerk und Industrie.</p>"
    "<p>Mit rund 45 Mitarbeitern an zwei Standorten.</p>"
    '<a href="/leistungen">Leistungen</a><a href="/intern">Intern</a>'
    '<script>var hidden = "Sonnenschutzfolie";</script></body></html>',
    "https://beta-werbung.test/leistungen": "<html><body><p>Sonnenschutzfolie und "
    "Sichtschutzfolie für Büros.</p></body></html>",
}
ROBOTS = {"beta-werbung.test": "User-agent: *\nDisallow: /intern\n"}


class FakeWeb:
    """Router über alle Testquellen; zählt Aufrufe je Host."""

    def __init__(self, failing_hosts: frozenset[str] = frozenset()) -> None:
        self.calls: Counter[str] = Counter()
        self.failing = failing_hosts

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls[host] += 1
        if host in self.failing or "*" in self.failing:
            raise httpx.ConnectError("Testnetz: Verbindung verweigert", request=request)
        route: Callable[[httpx.Request], httpx.Response] | None = {
            "api.gleif.org": self._gleif,
            "www.wikidata.org": self._wikidata,
            "query.wikidata.org": self._sparql,
            "www.sec.gov": self._sec_tickers,
            "data.sec.gov": self._sec_facts,
            "data-api.ecb.europa.eu": self._ecb,
            "api.ted.europa.eu": self._ted,
            "api.gdeltproject.org": self._gdelt,
        }.get(host)
        if route is not None:
            return route(request)
        return self._website(request)

    def _gleif(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("-parent"):
            return httpx.Response(404, json={"errors": [{"title": "Not Found"}]})
        if "/lei-records/" in path:
            lei = path.rsplit("/", 1)[-1]
            for records in GLEIF.values():
                for record in records:
                    if record["id"] == lei:
                        return httpx.Response(200, json={"data": record})
            return httpx.Response(404, json={"errors": [{"title": "Not Found"}]})
        query = request.url.params.get("filter[fulltext]", "")
        return httpx.Response(200, json={"data": GLEIF.get(query, [])})

    def _wikidata(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path.startswith("/wiki/Special:EntityData/"):
            qid = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            if qid not in ENTITIES:
                return httpx.Response(404, text="not found")
            body = json.dumps({"entities": {qid: ENTITIES[qid]}}, separators=(",", ":"))
            return httpx.Response(200, text=body, headers={"content-type": "application/json"})
        if params.get("action") == "wbsearchentities":
            qid = SEARCH.get(params.get("search", ""))
            hits = [{"id": qid, "label": params["search"], "description": "Test"}] if qid else []
            return httpx.Response(200, json={"search": hits})
        if params.get("action") == "wbgetentities":
            ids = params.get("ids", "").split("|")
            return httpx.Response(
                200, json={"entities": {i: ENTITIES[i] for i in ids if i in ENTITIES}}
            )
        return httpx.Response(400, text="bad request")

    def _sparql(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q900010"},
                            "itemLabel": {"value": "Beta Werbetechnik"},
                            "website": {"value": "https://beta-werbung.test/"},
                        }
                    ]
                }
            },
        )

    def _sec_tickers(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"0": {"cik_str": 999001, "ticker": "TST", "title": "3M CO"}}
        )

    def _sec_facts(self, request: httpx.Request) -> httpx.Response:
        cik = int(request.url.path.rsplit("CIK", 1)[-1].removesuffix(".json"))
        if cik not in SEC:
            return httpx.Response(404, text="no facts")
        return httpx.Response(200, json=SEC[cik])

    def _ecb(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ECB_CSV, headers={"content-type": "text/csv"})

    def _ted(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "Fahrzeugbeschriftung" not in body["query"]:
            return httpx.Response(200, json={"notices": [], "totalNoticeCount": 0})
        return httpx.Response(
            200,
            json={
                "notices": [
                    {
                        "publication-number": "000001-2026",
                        "notice-title": {
                            "deu": "Fahrzeugbeschriftung für den Fuhrpark der Teststadt"
                        },
                        "buyer-name": {"deu": ["Stadtwerke Teststadt"]},
                        "buyer-country": ["DEU"],
                        "publication-date": "2026-06-01+02:00",
                    }
                ],
                "totalNoticeCount": 1,
            },
        )

    def _gdelt(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://news.test/a1",
                        "title": "Beta Werbetechnik eröffnet neuen Standort",
                        "seendate": "20260901T100000Z",
                        "domain": "news.test",
                    }
                ]
            },
        )

    def _website(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/robots.txt":
            text = ROBOTS.get(request.url.host)
            return httpx.Response(200, text=text) if text else httpx.Response(404)
        if url in PAGES:
            return httpx.Response(
                200, text=PAGES[url], headers={"content-type": "text/html; charset=utf-8"}
            )
        return httpx.Response(404, text="nicht gefunden")
