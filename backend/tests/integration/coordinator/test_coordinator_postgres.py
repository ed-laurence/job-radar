"""The coordinator against real Postgres as the restricted app login (FORCE RLS applies): owner
scoping, NOTIFY'd steps, cross-task lease waits, Blocked on request_help, and the HTTP gate."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import QueuePool

from app.adapters.db.devboard_runs import PgRunStore
from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.db.engine import PrivilegedDatabaseRole
from app.adapters.db.notify import PgNotificationHub
from app.coordinator.config import CoordinatorSettings
from app.coordinator.server import build_app
from app.coordinator.service import Scope
from app.coordinator.tokens import mint, verify
from app.coordinator.wiring import CoordinatorRuntime, PgTaskOwners, build_runtime
from app.domain.devboard.model import Column, RunKind, RunRecord, RunStatus, StepKind, TaskRecord
from tests.integration.conftest import Database
from tests.integration.devboard.cleanup import leave_no_rows
from tests.unit.coordinator.support import BASE_URL, call

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture

type Sessions = async_sessionmaker[AsyncSession]
KEY = "coordinator-test-key-not-secret-0000000000000000"
NOW = datetime.now(UTC)


def _settings(url: str) -> CoordinatorSettings:
    return CoordinatorSettings(
        environment="test",
        guards="fake",
        database_url=SecretStr(url),
        coordinator_token_key=SecretStr(KEY),
        coordinator_allowed_hosts=("coordinator:8100",),
    )


@pytest.fixture
async def runtime(database: Database) -> AsyncGenerator[CoordinatorRuntime]:
    built = build_runtime(_settings(database.app_url))
    await built.start()
    try:
        yield built
    finally:
        await built.aclose()


async def _card(sessions: Sessions, owner: uuid.UUID, body: str = "Brief.") -> TaskRecord:
    task = TaskRecord(uuid.uuid4(), "Card", body, Column.AGENT_DEV, "U", 1, owner, NOW, NOW)
    await PgTaskStore(sessions).insert(owner, task)
    return task


async def _run(sessions: Sessions, task: TaskRecord, agent: str = "backend-engineer") -> RunRecord:
    run = RunRecord(
        uuid.uuid4(), task.id, RunKind.DEV, RunStatus.RUNNING, NOW, None, agent, budget_usd=1.0
    )
    await PgRunStore(sessions).insert(task.created_by, run)
    return run


async def _scope(runtime: CoordinatorRuntime, task: TaskRecord, run: RunRecord) -> Scope:
    token = mint(KEY, task_id=task.id, run_id=run.id, ttl_s=600)
    now = int(runtime.clock.now().timestamp())
    return await runtime.service.authorize(verify(KEY, token, now=now))


async def test_runs_as_the_restricted_login_with_a_pool_of_two(runtime: CoordinatorRuntime) -> None:
    assert runtime.engine is not None
    pool = runtime.engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 2


async def test_refuses_to_start_as_a_role_that_bypasses_rls(database: Database) -> None:
    built = build_runtime(_settings(database.owner_url))
    try:
        with pytest.raises(PrivilegedDatabaseRole):
            await built.start()
    finally:
        await built.aclose()


async def test_the_owner_is_the_cards_creator_not_the_lookup_scope(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID], owner: asyncpg.Connection
) -> None:
    _, user = two_users
    task = await _card(sessions, user)
    owners = PgTaskOwners(sessions)
    assert await owners.owner_of(task.id) == user
    assert await owners.owner_of(uuid.uuid4()) is None
    await owner.execute("UPDATE dev_tasks SET deleted_at = now() WHERE id = $1", task.id)
    assert await owners.owner_of(task.id) is None


async def test_steps_are_written_as_the_owner_redacted_and_notified(
    runtime: CoordinatorRuntime,
    sessions: Sessions,
    database: Database,
    two_users: tuple[uuid.UUID, uuid.UUID],
    owner: asyncpg.Connection,
) -> None:
    _, user = two_users
    task = await _card(sessions, user)
    run = await _run(sessions, task)
    scope = await _scope(runtime, task, run)
    assert scope.actor.user_id == user
    hub = PgNotificationHub(database.app_url)
    await hub.start()
    try:
        async with hub.subscribe("run_steps") as notes:
            text = "Ran the tests; saw ghp_" + "b" * 36
            await runtime.service.post_step(scope, StepKind.MESSAGE, text)
            note = await asyncio.wait_for(anext(notes), 5)
    finally:
        await hub.stop()
    assert json.loads(note.payload)["run_id"] == str(run.id)
    summary = cast(
        "str", await owner.fetchval("SELECT summary FROM run_steps WHERE run_id = $1", run.id)
    )
    assert isinstance(summary, str)
    assert "ghp_" not in summary


async def test_request_help_blocks_the_card_as_the_owners_agent(
    runtime: CoordinatorRuntime,
    sessions: Sessions,
    two_users: tuple[uuid.UUID, uuid.UUID],
    owner: asyncpg.Connection,
) -> None:
    _, user = two_users
    task = await _card(sessions, user)
    run = await _run(sessions, task)
    result = await runtime.service.request_help(
        await _scope(runtime, task, run), "Stuck on a lease."
    )
    assert result["column"] == "blocked"
    row = cast(
        "asyncpg.Record",
        await owner.fetchrow(
            'SELECT "column", blocked_reason FROM dev_tasks WHERE id = $1', task.id
        ),
    )
    assert (row["column"], row["blocked_reason"]) == ("blocked", "Stuck on a lease.")
    event = cast(
        "asyncpg.Record",
        await owner.fetchrow(
            "SELECT actor_kind, actor_user_id, run_id FROM task_events WHERE task_id = $1"
            " AND kind = 'moved' ORDER BY at DESC LIMIT 1",
            task.id,
        ),
    )
    assert (event["actor_kind"], event["actor_user_id"], event["run_id"]) == ("agent", user, run.id)


async def test_claim_paths_waits_for_another_tasks_lease_then_is_granted(
    runtime: CoordinatorRuntime,
    sessions: Sessions,
    two_users: tuple[uuid.UUID, uuid.UUID],
    owner: asyncpg.Connection,
) -> None:
    maintainer, user = two_users
    task, other = await _card(sessions, maintainer), await _card(sessions, user)
    first = await _scope(runtime, task, await _run(sessions, task))
    second = await _scope(runtime, other, await _run(sessions, other))
    held = await runtime.service.claim_paths(first, ("backend/src/app/api/jobs/",), 0)
    assert held["waiting"] == []

    async def release_soon() -> None:
        await asyncio.sleep(1.2)
        await runtime.service.release_paths(first)

    releaser = asyncio.create_task(release_soon())
    granted = await runtime.service.claim_paths(second, ("backend/src/app/api/jobs/x.py",), 10)
    await releaser
    assert granted["waiting"] == []
    assert 1 <= int(str(granted["waited_s"])) <= 5
    query = "SELECT count(*) FROM lease_waits WHERE run_id = $1"
    waits = cast("int", await owner.fetchval(query, second.run.id))
    assert waits == 1
    timed_out = await runtime.service.claim_paths(first, ("backend/src/app/api/",), 1)
    assert timed_out["waiting"] == ["backend/src/app/api/jobs/x.py"]
    assert str(second.task.id) not in json.dumps(timed_out)


async def test_the_http_gate_scopes_tokens_to_their_task(
    runtime: CoordinatorRuntime, sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, user = two_users
    mine = await _card(sessions, user, body="Mine.")
    my_run = await _run(sessions, mine)
    theirs = await _card(sessions, user, body="Theirs.")
    their_run = await _run(sessions, theirs)
    app = build_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http,
    ):
        good = mint(KEY, task_id=mine.id, run_id=my_run.id, ttl_s=600)
        page = await call(http, good, "GET", "/v1/task")
        assert page.status_code == 200
        assert page.json()["brief"] == "Mine."
        crossed = mint(KEY, task_id=mine.id, run_id=their_run.id, ttl_s=600)
        assert (await call(http, crossed, "GET", "/v1/task")).status_code == 403
