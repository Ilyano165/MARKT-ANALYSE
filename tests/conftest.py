"""Gemeinsame Testhilfen: Datenbank, Gateway mit Test-Internet, Kontext."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from market_intel import db
from market_intel.config import Settings
from market_intel.context import Ctx
from market_intel.http.gateway import FetchGateway
from market_intel.repo import sync_sources
from market_intel.usecases import load_use_cases, register

from .fakeweb import FakeWeb, public_resolver


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db(eng)
    return eng


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_ctx(
    engine: Engine, sleeps: list[float], tmp_path: Path
) -> Iterator[Callable[[FakeWeb], Ctx]]:
    created: list[FetchGateway] = []

    def factory(web: FakeWeb, **settings_overrides: object) -> Ctx:
        settings = Settings(
            database_url=str(engine.url),
            contact_email="test@example.org",
            output_dir=tmp_path / "out",
            **settings_overrides,  # type: ignore[arg-type]
        )
        active = sync_sources(engine, settings.enabled_sources, settings.disabled_sources, False)
        gateway = FetchGateway(
            engine,
            user_agent=settings.user_agent,
            enabled_sources=active,
            transport=web.transport(),
            resolver=public_resolver,
            sleep=sleeps.append,
        )
        created.append(gateway)
        register(engine, load_use_cases())
        return Ctx(engine=engine, gateway=gateway, settings=settings)

    yield factory
    for gateway in created:
        gateway.close()
