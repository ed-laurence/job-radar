"""Regression: the seed and the scan pool load and release once.

After `dev.sh --reset-db` both seeded accounts held a full copy of the demo data (600 visible
roles, 2,438 pooled), a scan reported duplicates, and `pool_remaining` disagreed with the table.
Now the demo data belongs to the owner only, released pool rows are marked consumed in the same
statement (and a re-seed doesn't bring them back), and every count names its user, so even a connection that bypasses RLS (a job run
with the owner URL) reports the true numbers.
"""

from __future__ import annotations

import json
import uuid
from typing import cast

import asyncpg
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.engine import create_engine, session_factory
from app.adapters.db.jobs_roles import PgRoles
from app.adapters.db.scan import PgKnownRoles, PgScanLock, PgScanRuns, PgSeedFeed
from app.adapters.fakes.scan import MemoryScanAccounts
from app.adapters.fakes.system import FakeClock
from app.adapters.system import UuidIds
from app.domain.identity import derive_user_id
from app.domain.jobs.scan import ScanService
from app.jobs.seed import load_seed
from app.settings import Settings
from tests.integration.conftest import Database

OWNER = "pool-owner@example.test"
TESTER = "pool-tester@example.test"
SEED_ROLES = 1519
VISIBLE = 300
BATCH = 5

type Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def both_seeded(database: Database) -> dict[str, int]:
    return await _seed(database)


async def _seed(database: Database) -> dict[str, int]:
    users = json.dumps([{"email": OWNER, "role": "maintainer"}, {"email": TESTER, "role": "user"}])
    settings = Settings(environment="test", app_users=SecretStr(users))
    engine = create_engine(database.owner_url, pool_size=1)
    try:
        return await load_seed(settings, engine, FakeClock())
    finally:
        await engine.dispose()


@pytest.fixture
async def privileged(database: Database) -> Sessions:
    # The owner URL: Postgres skips RLS for it, as for a job run with the admin DATABASE_URL.
    return session_factory(create_engine(database.owner_url, pool_size=2))


async def _counts(owner: asyncpg.Connection, user_id: uuid.UUID) -> tuple[int, int]:
    roles = await _count(owner, "SELECT count(*) FROM roles WHERE user_id = $1", user_id)
    waiting = "SELECT count(*) FROM role_pool WHERE user_id = $1 AND released_at IS NULL"
    return roles, await _count(owner, waiting, user_id)


async def _count(owner: asyncpg.Connection, sql: str, *args: object) -> int:
    # asyncpg values are untyped; every query here is a count.
    return int(cast("int", await owner.fetchval(sql, *args)))


async def _true_remaining(owner: asyncpg.Connection) -> int:
    return await _count(
        owner,
        "SELECT count(*) FROM role_pool p JOIN users u ON u.id = p.user_id"
        " WHERE u.disabled_at IS NULL AND p.released_at IS NULL",
    )


def _scan(sessions: Sessions, engine: AsyncEngine, users: list[uuid.UUID]) -> ScanService:
    clock = FakeClock()
    return ScanService(
        source=PgSeedFeed(sessions, clock, batch=BATCH),
        roles=PgRoles(sessions),
        known=PgKnownRoles(sessions),
        accounts=MemoryScanAccounts(users),
        runs=PgScanRuns(sessions),
        lock=PgScanLock(engine, key=7_202_698),
        clock=clock,
        ids=UuidIds(),
    )


async def test_the_demo_data_belongs_to_the_owner_only(
    both_seeded: dict[str, int], owner: asyncpg.Connection
) -> None:
    assert both_seeded[OWNER] > 10_000
    assert both_seeded[TESTER] == 0
    me = derive_user_id(OWNER)
    assert await _count(owner, "SELECT count(*) FROM roles WHERE user_id = $1", me) == VISIBLE
    pooled = await _count(owner, "SELECT count(*) FROM role_pool WHERE user_id = $1", me)
    assert pooled == SEED_ROLES - VISIBLE
    assert await _counts(owner, derive_user_id(TESTER)) == (0, 0)
    query = "SELECT count(*) FROM users WHERE id = $1 AND role = 'user'"
    assert await _count(owner, query, derive_user_id(TESTER)) == 1


@pytest.mark.parametrize("bypass_rls", [False, True])
async def test_scans_release_each_pool_row_once_and_count_it_truly(
    both_seeded: dict[str, int],
    database: Database,
    owner: asyncpg.Connection,
    sessions: Sessions,
    privileged: Sessions,
    app_engine: AsyncEngine,
    bypass_rls: bool,
) -> None:
    del both_seeded
    me, tester = derive_user_id(OWNER), derive_user_id(TESTER)
    scan_sessions = privileged if bypass_rls else sessions
    roles_before, pooled_before = await _counts(owner, me)
    feed = PgSeedFeed(scan_sessions, FakeClock(), batch=BATCH)
    assert await feed.remaining() == await _true_remaining(owner)

    service = _scan(scan_sessions, app_engine, [tester, me])
    first = await service.run()
    assert (first.fetched, first.new, first.duplicates) == (BATCH, BATCH, 0)
    assert await _counts(owner, me) == (roles_before + BATCH, pooled_before - BATCH)
    assert await _counts(owner, tester) == (0, 0)
    assert first.pool_remaining == await _true_remaining(owner)

    second = await service.run()
    assert (second.fetched, second.new, second.duplicates) == (BATCH, BATCH, 0)
    assert await _counts(owner, me) == (roles_before + 2 * BATCH, pooled_before - 2 * BATCH)
    assert second.pool_remaining == await _true_remaining(owner)
    # Re-running the seed (dev.sh does on every start) doesn't put released roles back.
    assert all(written == 0 for written in (await _seed(database)).values())
    assert await _counts(owner, me) == (roles_before + 2 * BATCH, pooled_before - 2 * BATCH)
    third = await service.run()
    assert (third.new, third.duplicates) == (BATCH, 0)
