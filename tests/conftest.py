"""Gemeinsame Testhilfen: Datenbank, Gateway mit Test-Internet, Kontext, Pipeline-Lauf."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from market_intel import db
from market_intel.config import Settings
from market_intel.context import Ctx
from market_intel.engine.budget import Budget
from market_intel.engine.strategy import ResearchRequest
from market_intel.http.gateway import FetchGateway
from market_intel.llm.assistant import Assistant
from market_intel.repo import sync_sources
from market_intel.research import create_research, execute
from market_intel.usecases import UseCase, load_use_cases, register

from .fakeweb import FakeWeb, public_resolver


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db(eng)
    return eng


@pytest.fixture
def cases(engine: Engine) -> dict[str, UseCase]:
    loaded = load_use_cases()
    register(engine, loaded)
    return loaded


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_ctx(
    engine: Engine, sleeps: list[float], tmp_path: Path, cases: dict[str, UseCase]
) -> Iterator[Callable[..., Ctx]]:
    created: list[FetchGateway] = []

    def factory(
        web: FakeWeb,
        budget: Budget | None = None,
        assistant: Assistant | None = None,
        **settings_overrides: Any,
    ) -> Ctx:
        settings = Settings(
            database_url=str(engine.url),
            contact_email="test@example.org",
            output_dir=tmp_path / "out",
        )
        settings = dataclasses.replace(settings, **settings_overrides)
        budget = budget or Budget()
        active = sync_sources(
            engine,
            settings.enabled_sources,
            settings.disabled_sources,
            settings.companies_house_key is not None,
        )
        gateway = FetchGateway(
            engine,
            user_agent=settings.user_agent,
            enabled_sources=active,
            transport=web.transport(),
            resolver=public_resolver,
            sleep=sleeps.append,
            max_requests=budget.max_requests,
            max_attempts=settings.max_attempts,
            min_intervals=dict(settings.rate_limits),
            cost_limit_eur=budget.max_cost_eur,
        )
        created.append(gateway)
        return Ctx(
            engine=engine,
            gateway=gateway,
            settings=settings,
            ai=assistant or Assistant(),
            budget=budget,
        )

    yield factory
    for gateway in created:
        gateway.close()


@pytest.fixture
def run_research(
    make_ctx: Callable[..., Ctx], engine: Engine, tmp_path: Path, cases: dict[str, UseCase]
) -> Callable[..., tuple[Ctx, int, str]]:
    def runner(
        request: ResearchRequest,
        web: FakeWeb | None = None,
        budget: Budget | None = None,
        **kwargs: Any,
    ) -> tuple[Ctx, int, str]:
        budget = budget or Budget()
        ctx = make_ctx(web or FakeWeb(), budget=budget, **kwargs)
        run_id = create_research(engine, request, budget, tmp_path / "out")
        status = execute(ctx, run_id, cases, progress=lambda _m: None)
        return ctx, run_id, status

    return runner
