"""Postgres adapters for the scan: the seed pool (`role_pool`), dedupe lookups, accounts, the
run record (`scan_runs`) and the single-flight advisory lock."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.engine import system_transaction, user_transaction
from app.adapters.db.tables_jobs import Role
from app.adapters.db.tables_scan import RolePool, ScanRun
from app.adapters.scrapers.seed_feed import DEFAULT_BATCH, SOURCE, scraped_role
from app.domain.ports import Clock
from app.domain.ports.scan import ScanRunRecord, ScrapedRole

type Sessions = async_sessionmaker[AsyncSession]

# One key for the whole app; small enough that pg_locks shows it as classid 0, objid KEY.
SCAN_LOCK_KEY = 7_202_611
_USERS = text("SELECT id FROM users WHERE disabled_at IS NULL ORDER BY created_at, id")
# Release marks the rows consumed (`released_at`) in the same statement that returns them, so a
# row is released once at most. They stay in `role_pool` so re-running the idempotent seed
# (dev.sh does on every start) can't put them back. Every query names the user explicitly as
# well as relying on RLS, so a privileged connection (a job run with the owner URL bypasses RLS)
# still counts and releases one account's pool only.
# The batch is picked once in a MATERIALIZED CTE: as an `IN (SELECT ... LIMIT ...)` subquery
# the planner may re-run it and release more than `limit` rows.
_RELEASE = text(
    "WITH picked AS MATERIALIZED (SELECT id FROM role_pool"
    " WHERE user_id = :user_id AND released_at IS NULL"
    " ORDER BY posted_at ASC NULLS LAST, external_id LIMIT :limit FOR UPDATE SKIP LOCKED)"
    " UPDATE role_pool AS p SET released_at = :now FROM picked"
    " WHERE p.id = picked.id AND p.user_id = :user_id"
    " RETURNING p.external_id, p.posted_at, p.payload"
)


async def _active_users(sessions: Sessions) -> list[uuid.UUID]:
    async with system_transaction(sessions) as session:
        return [row[0] for row in await session.execute(_USERS)]


class PgScanAccounts:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def active_users(self) -> list[uuid.UUID]:
        return await _active_users(self._sessions)


class PgSeedFeed:
    """`JobSource` over `role_pool`: each fetch releases (marks consumed and returns) the user's
    next `batch` roles; `remaining` counts the rows still waiting across active accounts."""

    name = SOURCE

    def __init__(self, sessions: Sessions, clock: Clock, batch: int = DEFAULT_BATCH) -> None:
        self._sessions = sessions
        self._clock = clock
        self._batch = batch

    async def fetch(self, user_id: uuid.UUID, since: datetime | None) -> list[ScrapedRole]:
        del since  # the pool has no posting window: every held-back role is new
        now = self._clock.now()
        async with user_transaction(self._sessions, user_id) as session:
            params = {"now": now, "user_id": user_id, "limit": self._batch}
            rows = list(await session.execute(_RELEASE, params))
        rows.sort(key=lambda row: (row[1] is None, row[1] or now, row[0]))
        return [scraped_role(row[0], row[2], now) for row in rows]

    async def remaining(self) -> int:
        total = 0
        for user_id in await _active_users(self._sessions):
            async with user_transaction(self._sessions, user_id) as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(RolePool)
                    .where(RolePool.user_id == user_id, RolePool.released_at.is_(None))
                )
                total += int(count or 0)
        return total


class PgKnownRoles:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def known(
        self, user_id: uuid.UUID, source: str, external_ids: tuple[str, ...]
    ) -> set[str]:
        async with user_transaction(self._sessions, user_id) as session:
            found = await session.scalars(
                select(Role.external_id).where(
                    Role.user_id == user_id,
                    Role.source == source,
                    Role.external_id.in_(external_ids),
                )
            )
            return {value for value in found if value is not None}


class PgScanRuns:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def save(self, run: ScanRunRecord) -> None:
        values = dataclasses.asdict(run)
        stmt = insert(ScanRun).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ScanRun.id], set_={k: v for k, v in values.items() if k != "id"}
        )
        async with system_transaction(self._sessions) as session:
            await session.execute(stmt)

    async def last(self) -> ScanRunRecord | None:
        async with system_transaction(self._sessions) as session:
            row = await session.scalar(
                select(ScanRun).order_by(ScanRun.started_at.desc(), ScanRun.id).limit(1)
            )
        if row is None:
            return None
        names = [f.name for f in dataclasses.fields(ScanRunRecord)]
        return ScanRunRecord(**{name: getattr(row, name) for name in names})


class PgScanLock:
    """A session advisory lock held on its own connection for the scan's duration."""

    def __init__(self, engine: AsyncEngine, key: int = SCAN_LOCK_KEY) -> None:
        self._engine = engine
        self._key = key

    @asynccontextmanager
    async def hold(self) -> AsyncGenerator[bool]:
        async with self._engine.connect() as connection:
            got = bool(
                await connection.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._key})
            )
            await connection.commit()
            try:
                yield got
            finally:
                if got:
                    unlock = text("SELECT pg_advisory_unlock(:k)")
                    await connection.execute(unlock, {"k": self._key})
                    await connection.commit()

    async def is_held(self) -> bool:
        query = text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory'"
            " AND classid = 0 AND objid = :k AND objsubid = 1 AND granted)"
        )
        async with self._engine.connect() as connection:
            return bool(await connection.scalar(query, {"k": self._key}))
