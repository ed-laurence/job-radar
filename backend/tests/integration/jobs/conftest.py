"""The real seed, loaded once per session into the integration database by `app.jobs.seed`."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import create_engine
from app.adapters.fakes.system import FakeClock
from app.domain.identity import derive_user_id
from app.jobs.seed import load_seed
from app.settings import Settings
from tests.integration.conftest import Database

SEED_OWNER = "seed-owner@example.test"


@dataclass(frozen=True)
class Seeded:
    user_id: uuid.UUID
    settings: Settings
    first: dict[str, int]
    second: dict[str, int]


async def _load_twice(
    settings: Settings, engine: AsyncEngine
) -> tuple[dict[str, int], dict[str, int]]:
    try:
        first = await load_seed(settings, engine, FakeClock())
        second = await load_seed(settings, engine, FakeClock())
        return first, second
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def seeded(database: Database) -> Seeded:
    # Only a maintainer is configured, so the tester's copy is skipped (faster, still mapped).
    users = json.dumps([{"email": SEED_OWNER, "role": "maintainer"}])
    settings = Settings(environment="test", app_users=SecretStr(users))
    engine = create_engine(database.owner_url, pool_size=1)
    first, second = asyncio.run(_load_twice(settings, engine))
    return Seeded(derive_user_id(SEED_OWNER), settings, first, second)
