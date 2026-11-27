"""Dev-board Postgres stores under the non-owner app role (FORCE RLS applies): versions, one
active run per task, numbered steps with NOTIFY, atomic leases, and the jobs' SQL."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_leases import PgLeaseStore
from app.adapters.db.devboard_runs import PgRunStore
from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.db.notify import PgNotificationHub
from app.domain.devboard.model import (
    Area,
    Column,
    Criterion,
    LeaseKind,
    NewStep,
    PlanRecord,
    Risk,
    RunKind,
    RunRecord,
    RunStatus,
    Size,
    StepKind,
    Subtask,
    TaskRecord,
    TaskType,
    TriageRecord,
)
from app.domain.errors import Conflict, NotFound
from app.domain.ports.devboard import LeaseClaim, RunFinish
from app.jobs.digest import PgBoardActivity, PgDigestStore
from app.jobs.feed_replay import PgHeldBackRoles
from app.jobs.rollup import PgWebEventRetention
from tests.integration.conftest import Database
from tests.integration.devboard.cleanup import leave_no_rows

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)


def _task(user: uuid.UUID, rank: str = "U") -> TaskRecord:
    return TaskRecord(uuid.uuid4(), "Card", "Body", Column.BACKLOG, rank, 1, user, NOW, NOW)


def _run(task_id: uuid.UUID, parent: uuid.UUID | None = None) -> RunRecord:
    return RunRecord(uuid.uuid4(), task_id, RunKind.DEV, RunStatus.QUEUED, NOW, parent, "dev-lead")


async def test_task_versions_ranks_triage_and_plans(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, user = two_users
    store = PgTaskStore(sessions)
    task = _task(user)
    await store.insert(user, task)
    loaded = await store.get(maintainer, task.id)
    assert loaded is not None
    assert loaded.created_by_role is not None
    assert loaded.created_by_role.value == "user"
    saved = await store.save(maintainer, loaded, 1)
    assert saved.version == 2
    with pytest.raises(Conflict) as stale:
        await store.save(maintainer, loaded, 1)
    assert stale.value.current_version == 2
    await store.insert(user, _task(user, "k"))
    assert [rank for rank, _ in await store.column_ranks(user, Column.BACKLOG)][:2] == ["U", "k"]
    triage = TriageRecord(
        task_id=task.id,
        type=TaskType.FEATURE,
        size=Size.S,
        risk=Risk.LOW,
        areas=(Area.API,),
        criteria=(Criterion("backend/tests/unit/test_x.py::y", "z"),),
        needs_human_design=False,
        in_scope=True,
        created_at=NOW,
    )
    await store.add_triage(user, triage)
    assert await store.latest_triage(user, task.id) == triage
    plan = PlanRecord(
        uuid.uuid4(),
        task.id,
        1,
        "single",
        (Subtask("t0", "tests", "qa-expert", ("backend/tests/unit/",), is_test_only=True),),
        ("backend/tests/unit/test_x.py::y",),
        2.0,
        30,
        Risk.MEDIUM,
        NOW,
    )
    await store.add_plan(user, plan)
    assert await store.latest_plan(user, task.id) == plan
    approved = await store.save_plan(user, plan, 1)
    assert approved.version == 2
    await store.delete(user, task.id, 2, NOW)
    assert await store.get(user, task.id) is None
    with pytest.raises(NotFound):
        await store.delete(user, task.id, 3, NOW)


async def test_one_active_top_level_run_and_numbered_steps(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, _ = two_users
    tasks, runs = PgTaskStore(sessions), PgRunStore(sessions)
    task = _task(maintainer)
    await tasks.insert(maintainer, task)
    run = _run(task.id)
    await runs.insert(maintainer, run)
    with pytest.raises(Conflict):
        await runs.insert(maintainer, _run(task.id))
    await runs.insert(maintainer, _run(task.id, parent=run.id))  # children are fine
    loaded = await tasks.get(maintainer, task.id)
    assert loaded is not None
    assert loaded.active_run_id == run.id
    steps = await asyncio.gather(
        *(
            runs.append_step(maintainer, run.id, NewStep(StepKind.THOUGHT, f"s{n}"), NOW)
            for n in range(5)
        )
    )
    assert sorted(step.seq for step in steps) == [1, 2, 3, 4, 5]
    finish = RunFinish(RunStatus.SUCCEEDED, "ok", NOW, cost_usd=0.25)
    done = await runs.finish(maintainer, run.id, finish, NewStep(StepKind.STATUS, "done"))
    assert done is not None
    assert done.status is RunStatus.SUCCEEDED
    assert await runs.finish(maintainer, run.id, finish, NewStep(StepKind.STATUS, "again")) is None
    after = await runs.steps_after(maintainer, run.id, 4, 10)
    assert [step.seq for step in after] == [5, 6]
    await runs.insert(maintainer, _run(task.id))  # the first one finished


async def test_step_inserts_notify_the_shared_hub(
    sessions: Sessions, database: Database, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, _ = two_users
    tasks, runs = PgTaskStore(sessions), PgRunStore(sessions)
    task = _task(maintainer)
    await tasks.insert(maintainer, task)
    run = _run(task.id)
    await runs.insert(maintainer, run)
    hub = PgNotificationHub(database.app_url)
    await hub.start()
    try:
        async with hub.subscribe("run_steps") as notes:
            await runs.append_step(maintainer, run.id, NewStep(StepKind.MESSAGE, "hello"), NOW)
            note = await asyncio.wait_for(anext(notes), 5)
        assert json.loads(note.payload)["run_id"] == str(run.id)
    finally:
        await hub.stop()


async def test_leases_are_atomic_and_reclaimable(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, _ = two_users
    tasks, runs, leases = PgTaskStore(sessions), PgRunStore(sessions), PgLeaseStore(sessions)
    task = _task(maintainer)
    await tasks.insert(maintainer, task)
    first = _run(task.id)
    await runs.insert(maintainer, first)
    second = _run(task.id, parent=first.id)
    await runs.insert(maintainer, second)

    def claim(run: RunRecord, path: str, minutes: int = 10) -> LeaseClaim:
        kind = LeaseKind.HOTSPOT if path == "web/src/lib/" else LeaseKind.EXCLUSIVE
        return LeaseClaim(run.id, task.id, ((path, kind),), NOW, NOW + timedelta(minutes=minutes))

    outcomes = await asyncio.gather(
        leases.claim(maintainer, claim(first, "web/src/lib/")),
        leases.claim(maintainer, claim(second, "web/src/lib/")),
    )
    assert sorted(bool(outcome.granted) for outcome in outcomes) == [False, True]
    winner = first if outcomes[0].granted else second
    assert [lease.run_id for lease in await leases.active(maintainer, NOW)] == [winner.id]
    later = NOW + timedelta(minutes=11)
    assert await leases.reclaim_expired(maintainer, later) == 1
    loser = second if winner is first else first
    hotspot = (("web/src/lib/", LeaseKind.HOTSPOT),)
    regrant = await leases.claim(
        maintainer, LeaseClaim(loser.id, task.id, hotspot, later, later + timedelta(minutes=5))
    )
    assert regrant.granted
    assert await leases.renew(maintainer, loser.id, later, later + timedelta(minutes=9)) == 1
    assert await leases.release(maintainer, later, task_id=task.id) == 1


async def test_job_sql(
    sessions: Sessions, owner: asyncpg.Connection, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    maintainer, user = two_users
    await owner.execute(
        "INSERT INTO roles (user_id, source, title, found_at, rank, release_at)"
        " VALUES ($1, 'seed', 'Engineer', $2, 'a', $3), ($1, 'seed', 'Later', $2, 'b', $4)",
        user,
        NOW - timedelta(days=3),
        NOW - timedelta(hours=1),
        NOW + timedelta(days=1),
    )
    released = await PgHeldBackRoles(sessions).release(NOW, 20)
    assert [role.user_id for role in released] == [user]
    assert await owner.fetchval("SELECT count(*) FROM roles WHERE release_at IS NOT NULL") == 1
    retention = PgWebEventRetention(sessions)
    await retention.ensure_partitions(NOW.date())
    assert await retention.purge_before(NOW.date() - timedelta(days=90)) == 0
    activity = await PgBoardActivity(sessions).since(maintainer, NOW - timedelta(days=1))
    assert isinstance(activity.columns, dict)
    await PgDigestStore(sessions).save("digest:latest", {"headline": "h"})
    await PgDigestStore(sessions).save("digest:latest", {"headline": "h2"})
    stored = cast(
        "str | None",
        await owner.fetchval(
            "SELECT value->>'headline' FROM app_state WHERE key = 'digest:latest'"
        ),
    )
    assert stored == "h2"
