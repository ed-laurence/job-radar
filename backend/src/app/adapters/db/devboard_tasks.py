"""Postgres adapter for dev-board tasks, events, triage and plans. Every statement runs in
`user_transaction`, so the shared board tables are visible only with `app.user_id` set (SEC-03)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_mappers import (
    plan_record,
    plan_values,
    task_record,
    task_select,
    task_values,
    triage_record,
    triage_values,
)
from app.adapters.db.devboard_reviews import review_record, review_values
from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_board import (
    DevTask,
    TaskEvent,
    TaskPlan,
    TaskReview,
    TaskTriage,
)
from app.domain.devboard.model import (
    COLUMN_ORDER,
    ActorKind,
    Column,
    EventKind,
    PlanRecord,
    ReviewRecord,
    TaskEventRecord,
    TaskRecord,
    TriageRecord,
)
from app.domain.errors import Conflict, NotFound

type Sessions = async_sessionmaker[AsyncSession]

_ORDER = case(
    {column.value: index for index, column in enumerate(COLUMN_ORDER)}, value=DevTask.column
)


async def _stale(
    session: AsyncSession, table: type[DevTask] | type[TaskPlan], row_id: uuid.UUID, what: str
) -> Exception:
    """After a failed compare-and-set: `Conflict` with the current version, or `NotFound`."""
    query = select(table.version).where(table.id == row_id)
    if table is DevTask:
        query = query.where(DevTask.deleted_at.is_(None))
    current = await session.scalar(query)
    if current is None:
        return NotFound(f"{what} not found.")
    return Conflict(f"The {what.lower()} changed elsewhere.", current_version=int(current))


# ------------------------------------------------------------------ tasks
class PgTaskStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def insert(self, user_id: uuid.UUID, task: TaskRecord) -> None:
        values = task_values(task) | {
            "id": task.id,
            "version": task.version,
            "created_by": task.created_by,
            "created_at": task.created_at,
        }
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(insert(DevTask).values(values))

    async def get(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = (await session.execute(task_select().where(DevTask.id == task_id))).first()
        return None if row is None else task_record(row)

    async def list(
        self, user_id: uuid.UUID, columns: tuple[Column, ...], offset: int, limit: int
    ) -> list[TaskRecord]:
        query = task_select().order_by(_ORDER, DevTask.rank, DevTask.id)
        if columns:
            query = query.where(DevTask.column.in_([column.value for column in columns]))
        async with user_transaction(self._sessions, user_id) as session:
            rows = (await session.execute(query.offset(offset).limit(limit))).all()
        return [task_record(row) for row in rows]

    async def save(self, user_id: uuid.UUID, task: TaskRecord, expected_version: int) -> TaskRecord:
        statement = (
            update(DevTask)
            .where(
                DevTask.id == task.id,
                DevTask.version == expected_version,
                DevTask.deleted_at.is_(None),
            )
            .values(task_values(task) | {"version": expected_version + 1})
            .returning(DevTask.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            if await session.scalar(statement) is None:
                raise await _stale(session, DevTask, task.id, "Task")
            row = (await session.execute(task_select().where(DevTask.id == task.id))).one()
        return task_record(row)

    async def delete(
        self, user_id: uuid.UUID, task_id: uuid.UUID, expected_version: int, at: datetime
    ) -> None:
        statement = (
            update(DevTask)
            .where(
                DevTask.id == task_id,
                DevTask.version == expected_version,
                DevTask.deleted_at.is_(None),
            )
            .values(deleted_at=at, version=expected_version + 1, updated_at=at)
            .returning(DevTask.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            if await session.scalar(statement) is None:
                raise await _stale(session, DevTask, task_id, "Task")

    async def column_ranks(self, user_id: uuid.UUID, column: Column) -> list[tuple[str, uuid.UUID]]:
        query = (
            select(DevTask.rank, DevTask.id)
            .where(DevTask.column == column.value, DevTask.deleted_at.is_(None))
            .order_by(DevTask.rank, DevTask.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = (await session.execute(query)).all()
        return [(str(row[0]), row[1]) for row in rows]

    async def set_ranks(self, user_id: uuid.UUID, ranks: dict[uuid.UUID, str]) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            for task_id, rank in ranks.items():
                await session.execute(
                    update(DevTask)
                    .where(DevTask.id == task_id)
                    .values(rank=rank, version=DevTask.version + 1)
                )

    async def add_event(self, user_id: uuid.UUID, event: TaskEventRecord) -> None:
        values = {
            "id": event.id,
            "task_id": event.task_id,
            "at": event.at,
            "kind": event.kind.value,
            "actor_kind": event.actor_kind.value,
            "actor_user_id": event.actor_user_id,
            "from_column": event.from_column.value if event.from_column else None,
            "to_column": event.to_column.value if event.to_column else None,
            "run_id": event.run_id,
        }
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(insert(TaskEvent).values(values))

    async def events(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskEventRecord]:
        query = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.at.desc(), TaskEvent.created_at.desc(), TaskEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = (await session.scalars(query)).all()
        return [
            TaskEventRecord(
                id=row.id,
                task_id=row.task_id,
                at=row.at,
                kind=EventKind(row.kind),
                actor_kind=ActorKind(row.actor_kind),
                actor_user_id=row.actor_user_id,
                from_column=Column(row.from_column) if row.from_column else None,
                to_column=Column(row.to_column) if row.to_column else None,
                run_id=row.run_id,
            )
            for row in rows
        ]

    async def add_triage(self, user_id: uuid.UUID, triage: TriageRecord) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(insert(TaskTriage).values(triage_values(triage)))

    async def latest_triage(self, user_id: uuid.UUID, task_id: uuid.UUID) -> TriageRecord | None:
        query = (
            select(TaskTriage)
            .where(TaskTriage.task_id == task_id)
            .order_by(TaskTriage.created_at.desc(), TaskTriage.id.desc())
            .limit(1)
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(query)
        return None if row is None else triage_record(row)

    async def add_plan(self, user_id: uuid.UUID, plan: PlanRecord) -> None:
        values = plan_values(plan) | {
            "id": plan.id,
            "task_id": plan.task_id,
            "version": plan.version,
            "created_at": plan.created_at,
            "updated_at": plan.created_at,
        }
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(insert(TaskPlan).values(values))

    async def latest_plan(self, user_id: uuid.UUID, task_id: uuid.UUID) -> PlanRecord | None:
        query = (
            select(TaskPlan)
            .where(TaskPlan.task_id == task_id)
            .order_by(TaskPlan.version.desc(), TaskPlan.created_at.desc())
            .limit(1)
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(query)
        return None if row is None else plan_record(row)

    async def add_review(self, user_id: uuid.UUID, review: ReviewRecord) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(insert(TaskReview).values(review_values(review)))

    async def latest_review(self, user_id: uuid.UUID, task_id: uuid.UUID) -> ReviewRecord | None:
        query = (
            select(TaskReview)
            .where(TaskReview.task_id == task_id)
            .order_by(TaskReview.created_at.desc(), TaskReview.round.desc(), TaskReview.id.desc())
            .limit(1)
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(query)
        return None if row is None else review_record(row)

    async def save_plan(
        self, user_id: uuid.UUID, plan: PlanRecord, expected_version: int
    ) -> PlanRecord:
        values = plan_values(plan) | {"version": expected_version + 1, "updated_at": func.now()}
        statement = (
            update(TaskPlan)
            .where(TaskPlan.id == plan.id, TaskPlan.version == expected_version)
            .values(values)
            .returning(TaskPlan)
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(statement)
            if row is None:
                raise await _stale(session, TaskPlan, plan.id, "Plan")
            return plan_record(row)
