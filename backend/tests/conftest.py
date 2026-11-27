"""Shared fixtures: an app wired entirely with fakes (no network, no database)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.fakes.data import InMemoryUsageStore, InMemoryWebEvents
from app.adapters.fakes.security import (
    FakeContentGuard,
    FakePII,
    FakeTokenVerifier,
    InMemoryRateLimiter,
)
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.adapters.llm.providers.replay import ReplayProvider
from app.adapters.llm.routing import load_routing
from app.adapters.llm.service import LlmService
from app.main import Container, build_app
from app.settings import Settings

MAINTAINER = "maintainer@example.test"
USER = "user@example.test"
APP_USERS = json.dumps(
    [
        {"email": MAINTAINER, "role": "maintainer", "display_name": "Maintainer"},
        {"email": USER, "role": "user"},
    ]
)


def auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer fake:{email}"}


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", app_users=SecretStr(APP_USERS), max_body_bytes=4096)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def container(settings: Settings, clock: FakeClock) -> Container:
    usage = InMemoryUsageStore()
    guard, pii = FakeContentGuard(), FakePII()
    replay = ReplayProvider(settings.llm_fixtures_dir)
    llm = LlmService(
        load_routing(settings.models_config),
        {"vertex-claude": replay, "vertex-maas": replay, "vertex-gemini": replay},
        guard,
        pii,
        usage,
        clock,
    )
    return Container(
        settings=settings,
        clock=clock,
        ids=SequentialIds(),
        verifier=FakeTokenVerifier(),
        rate_limiter=InMemoryRateLimiter(clock),
        web_events=InMemoryWebEvents(),
        usage=usage,
        guard=guard,
        pii=pii,
        llm=llm,
    )


@pytest.fixture
async def client(container: Container) -> AsyncGenerator[httpx.AsyncClient]:
    app = build_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http


def iso(moment: datetime) -> str:
    return moment.isoformat()
