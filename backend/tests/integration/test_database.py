"""Migrations, row-level security, NOTIFY fan-out and the Postgres repositories."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import system_transaction, user_transaction
from app.adapters.db.notify import PgNotificationHub
from app.adapters.db.repositories import PgLlmUsageStore, PgRateLimiter, PgWebEvents
from app.adapters.fakes.system import FakeClock
from app.domain.ports import LlmCallRecord, LlmFeature, StoredWebEvent
from app.domain.rate_limits import RateTier, TierPolicy
from app.settings import REPO_ROOT
from tests.integration.conftest import Database

pytestmark = pytest.mark.integration

type Sessions = async_sessionmaker[AsyncSession]

_INSERT_ROLE = text(
    "INSERT INTO roles (user_id, source, title, found_at, rank)"
    " VALUES (:user_id, 'manual', 'Platform engineer', now(), 'a')"
)


async def test_user_owned_rows_are_isolated(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    async with user_transaction(sessions, first) as session:
        await session.execute(_INSERT_ROLE, {"user_id": first})
    async with user_transaction(sessions, second) as session:
        seen = await session.scalar(
            text("SELECT count(*) FROM roles WHERE user_id = :u"), {"u": first}
        )
        assert seen == 0
    async with user_transaction(sessions, first) as session:
        assert await session.scalar(text("SELECT count(*) FROM roles")) >= 1


async def test_cannot_write_rows_for_another_user(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_transaction(sessions, first) as session:
            await session.execute(_INSERT_ROLE, {"user_id": second})


async def test_nothing_is_visible_without_a_user(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, _ = two_users
    async with user_transaction(sessions, first) as session:
        await session.execute(_INSERT_ROLE, {"user_id": first})
    # A pooled connection that ran set_config before reads '' afterwards: must still be empty.
    for _ in range(2):
        async with system_transaction(sessions) as session:
            assert await session.scalar(text("SELECT count(*) FROM roles")) == 0
            assert await session.scalar(text("SELECT count(*) FROM dev_tasks")) == 0


async def test_shared_tables_need_a_user_to_write(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    insert = text("INSERT INTO dev_tasks (title, rank, created_by) VALUES ('t', 'a', :u)")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with system_transaction(sessions) as session:
            await session.execute(insert, {"u": first})
    async with user_transaction(sessions, first) as session:
        await session.execute(insert, {"u": first})
    async with user_transaction(sessions, second) as session:
        assert await session.scalar(text("SELECT count(*) FROM dev_tasks")) >= 1


async def test_app_role_cannot_create_users(sessions: Sessions) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with system_transaction(sessions) as session:
            await session.execute(
                text("INSERT INTO users (email, role) VALUES ('x@example.test', 'user')")
            )


async def test_run_step_insert_notifies_listeners(
    database: Database, sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, _ = two_users
    hub = PgNotificationHub(database.app_url)
    await hub.start()
    try:
        async with hub.subscribe("run_steps") as notes:
            async with user_transaction(sessions, first) as session:
                task = await session.scalar(
                    text(
                        "INSERT INTO dev_tasks (title, rank, created_by) VALUES ('t', 'b', :u) RETURNING id"
                    ),
                    {"u": first},
                )
                run = await session.scalar(
                    text("INSERT INTO runs (task_id, kind) VALUES (:t, 'dev') RETURNING id"),
                    {"t": task},
                )
                await session.execute(
                    text(
                        "INSERT INTO run_steps (run_id, seq, kind, summary, at) VALUES (:r, 1, 'status', 'go', now())"
                    ),
                    {"r": run},
                )
            note = await asyncio.wait_for(anext(aiter(notes)), timeout=5)
        assert str(run) in note.payload
        assert "go" not in note.payload  # ids only, never content
    finally:
        await hub.stop()


async def test_one_active_top_level_run_per_task(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, _ = two_users
    with pytest.raises(DBAPIError, match="uq_runs_one_active_top_level"):
        async with user_transaction(sessions, first) as session:
            task = await session.scalar(
                text(
                    "INSERT INTO dev_tasks (title, rank, created_by) VALUES ('t', 'c', :u) RETURNING id"
                ),
                {"u": first},
            )
            for _ in range(2):
                await session.execute(
                    text("INSERT INTO runs (task_id, kind) VALUES (:t, 'dev')"), {"t": task}
                )


async def test_rate_limiter_takes_and_refills_tokens(sessions: Sessions) -> None:
    clock = FakeClock()
    limiter = PgRateLimiter(sessions, clock)
    policy = TierPolicy(capacity=2, refill_per_minute=60)
    key = f"user:{uuid.uuid4()}"
    decisions = [await limiter.consume(key, RateTier.AI, policy) for _ in range(3)]
    assert [d.is_allowed for d in decisions] == [True, True, False]
    assert decisions[2].retry_after_s >= 1
    clock.advance(timedelta(seconds=1))
    assert (await limiter.consume(key, RateTier.AI, policy)).is_allowed


async def test_web_events_round_trip(sessions: Sessions) -> None:
    repo = PgWebEvents(sessions)
    day = datetime.now(UTC).date()
    at = datetime.now(UTC)
    session_id = uuid.uuid4().hex[:20]
    events = [
        StoredWebEvent(at, day, session_id, "user", "page_view", "/it-route"),
        StoredWebEvent(at, day, session_id, "user", "product", "/", name="role_created"),
        StoredWebEvent(
            at, day, session_id, "user", "web_vital", "/it-route", metric="LCP", value=100.0
        ),
        StoredWebEvent(
            at, day, session_id, "user", "web_vital", "/it-route", metric="LCP", value=200.0
        ),
    ]
    assert await repo.insert_many(events) == 4
    end = day + timedelta(days=1)
    views = [v for v in await repo.page_views(day, end) if v.route == "/it-route"]
    assert views[0].views == 1
    assert (session_id, "role_created") in await repo.product_event_sessions(day, end)
    vitals = [v for v in await repo.vitals_p75(day, end) if v.route == "/it-route"]
    assert vitals[0].p75 == pytest.approx(175.0)


async def test_llm_usage_is_logged_under_rls_and_spend_accumulates(
    sessions: Sessions, owner: asyncpg.Connection, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, _ = two_users
    store = PgLlmUsageStore(sessions)
    day = date(2026, 11, 25)
    record = LlmCallRecord(
        user_id=first,
        at=datetime.now(UTC),
        feature=LlmFeature.SCORE,
        provider="vertex-claude",
        model="claude-haiku-5-5",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cost_usd=0.0012,
        latency_ms=40,
        is_ok=True,
        is_fallback=False,
        guard_input="pass",
        guard_output="pass",
    )
    await store.record_call(record)
    await store.add_spend(day, LlmFeature.SCORE, 0.5)
    await store.add_spend(day, LlmFeature.SCORE, 0.25)
    assert await store.spent(day, LlmFeature.SCORE) == pytest.approx(0.75)
    assert await owner.fetchval("SELECT count(*) FROM llm_calls WHERE user_id = $1", first) == 1


def test_downgrade_and_upgrade_round_trip(database: Database) -> None:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.owner_url)
    command.check(config)  # models and migrations agree
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def test_research_steps_share_run_steps_but_stay_private(
    database: Database, sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    hub = PgNotificationHub(database.app_url)
    await hub.start()
    try:
        async with hub.subscribe("run_steps") as notes:
            async with user_transaction(sessions, first) as session:
                target = await session.scalar(
                    text(
                        "INSERT INTO targets (user_id, name, name_key, lane, origin) VALUES (:u, 'Acme', :k, 'A', 'manual') RETURNING id"
                    ),
                    {"u": first, "k": f"acme-{uuid.uuid4()}"},
                )
                research = await session.scalar(
                    text(
                        "INSERT INTO research_runs (user_id, target_id) VALUES (:u, :t) RETURNING id"
                    ),
                    {"u": first, "t": target},
                )
                await session.execute(
                    text(
                        "INSERT INTO run_steps (research_run_id, user_id, seq, kind, summary, at) VALUES (:r, :u, 1, 'status', 'go', now())"
                    ),
                    {"r": research, "u": first},
                )
            note = await asyncio.wait_for(anext(aiter(notes)), timeout=5)
        assert str(research) in note.payload
    finally:
        await hub.stop()
    count = text("SELECT count(*) FROM run_steps WHERE research_run_id = :r")
    async with user_transaction(sessions, second) as session:
        assert await session.scalar(count, {"r": research}) == 0
    async with user_transaction(sessions, first) as session:
        assert await session.scalar(count, {"r": research}) == 1
    with pytest.raises(DBAPIError, match="ck_run_steps_one_run"):
        async with user_transaction(sessions, first) as session:
            await session.execute(
                text(
                    "INSERT INTO run_steps (seq, kind, summary, at) VALUES (1, 'status', 'x', now())"
                )
            )
