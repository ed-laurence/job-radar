"""Postgres scan adapters: the seed pool releases five oldest-posted roles per fetch and then
nothing, under RLS; dedupe lookups; the run record; the advisory lock; and `profile_documents`
row-level security."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction
from app.adapters.db.profile_documents import PgManifests
from app.adapters.db.scan import PgKnownRoles, PgScanLock, PgScanRuns, PgSeedFeed
from app.adapters.fakes.system import FakeClock
from app.domain.ports.scan import ScanRunRecord

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


async def _pool(owner: asyncpg.Connection, user_id: uuid.UUID, count: int) -> None:
    for n in range(count):
        await owner.execute(
            "INSERT INTO role_pool (user_id, source, external_id, posted_at, payload)"
            " VALUES ($1, 'seed', $2, $3, $4::jsonb)",
            user_id,
            f"pool-{n:02d}",
            NOW - timedelta(days=n),
            json.dumps({"title": f"Role {n}"}),
        )


async def test_the_pool_releases_five_oldest_first_then_nothing(
    sessions: Sessions, owner: asyncpg.Connection, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    await _pool(owner, first, 7)
    feed = PgSeedFeed(sessions, FakeClock(NOW), batch=5)
    batch = await feed.fetch(first, None)
    assert [r.external_id for r in batch] == ["pool-06", "pool-05", "pool-04", "pool-03", "pool-02"]
    assert all(r.found_at == NOW and r.source == "seed" for r in batch)
    assert [r.external_id for r in await feed.fetch(first, None)] == ["pool-01", "pool-00"]
    assert await feed.fetch(first, None) == []
    # Another user's pool is invisible (RLS): nothing to release for them.
    assert await feed.fetch(second, None) == []
    query = "SELECT count(*) FROM role_pool WHERE user_id = $1 AND released_at IS NULL"
    assert await owner.fetchval(query, first) == 0


async def test_known_roles_dedupe_on_source_and_external_id(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    async with user_transaction(sessions, first) as session:
        await session.execute(
            text(
                "INSERT INTO roles (user_id, source, external_id, title, found_at, rank)"
                " VALUES (:u, 'seed', 'dup-1', 'Engineer', now(), 'a')"
            ),
            {"u": first},
        )
    known = PgKnownRoles(sessions)
    assert await known.known(first, "seed", ("dup-1", "new-1")) == {"dup-1"}
    assert await known.known(first, "manual", ("dup-1",)) == set()
    assert await known.known(second, "seed", ("dup-1",)) == set()


async def test_scan_runs_round_trip(sessions: Sessions) -> None:
    runs = PgScanRuns(sessions)
    run = ScanRunRecord(
        id=uuid.uuid4(),
        started_at=NOW + timedelta(days=30),
        trigger="manual",
        source="seed",
        status="running",
    )
    await runs.save(run)
    done = dataclasses.replace(run, status="ok", new=5, finished_at=NOW)
    await runs.save(done)
    assert await runs.last() == done


async def test_the_advisory_lock_is_single_flight(app_engine: AsyncEngine) -> None:
    lock = PgScanLock(app_engine, key=7_202_699)
    async with lock.hold() as first:
        assert first
        assert await lock.is_held()
        async with lock.hold() as second:
            assert not second
    assert not await lock.is_held()


async def test_profile_documents_are_private_to_their_owner(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    manifests = PgManifests(sessions)
    assert await manifests.load(first) is None
    await manifests.save(first, {"documents": {"cv_technical": {"filename": "cv.pdf"}}})
    await manifests.save(first, {"documents": {}})
    assert await manifests.load(first) == {"documents": {}}
    assert await manifests.load(second) is None
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_transaction(sessions, second) as session:
            await session.execute(
                text("INSERT INTO profile_documents (user_id, manifest) VALUES (:u, '{}')"),
                {"u": first},
            )
