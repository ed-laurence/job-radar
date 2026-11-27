"""Postgres adapter for agent runs and their steps (`run_steps`, which notifies on insert),
under `user_transaction` (SEC-03)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_mappers import (
    ACTIVE_STATUSES,
    money,
    run_record,
    step_record,
)
from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_board import (
    Run,
    RunStep,
)
from app.domain.devboard.model import (
    NewStep,
    RunRecord,
    RunStatus,
    StepRecord,
)
from app.domain.errors import Conflict, NotFound
from app.domain.ports.devboard import RunFinish

type Sessions = async_sessionmaker[AsyncSession]


async def _next_step(
    session: AsyncSession, run_id: uuid.UUID, step: NewStep, at: datetime
) -> StepRecord:
    """Caller holds the run row lock, so `max(seq) + 1` can't race."""
    last = await session.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == run_id))
    row = await session.scalar(
        insert(RunStep)
        .values(
            run_id=run_id,
            seq=int(last or 0) + 1,
            kind=step.kind.value,
            agent=step.agent,
            tool=step.tool,
            summary=step.summary,
            at=at,
        )
        .returning(RunStep)
    )
    assert row is not None
    return step_record(row)


class PgRunStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def insert(self, user_id: uuid.UUID, run: RunRecord) -> None:
        values = {
            "id": run.id,
            "task_id": run.task_id,
            "parent_run_id": run.parent_run_id,
            "kind": run.kind.value,
            "agent": run.agent,
            "status": run.status.value,
            "round": run.round,
            "model": run.model,
            "provider": run.provider,
            "budget_usd": None if run.budget_usd is None else money(run.budget_usd),
            "created_at": run.created_at,
        }
        try:
            async with user_transaction(self._sessions, user_id) as session:
                await session.execute(insert(Run).values(values))
        except IntegrityError:
            # `uq_runs_one_active_top_level`: one queued or running top-level run per task.
            raise Conflict("This card already has an active run.") from None

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> RunRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.get(Run, run_id)
            return None if row is None else run_record(row)

    async def for_task(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[RunRecord]:
        query = (
            select(Run)
            .where(Run.task_id == task_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [run_record(row) for row in (await session.scalars(query)).all()]

    async def active(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[RunRecord]:
        query = select(Run).where(Run.task_id == task_id, Run.status.in_(ACTIVE_STATUSES))
        async with user_transaction(self._sessions, user_id) as session:
            return [run_record(row) for row in (await session.scalars(query)).all()]

    async def mark_running(self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime) -> None:
        statement = (
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.QUEUED.value)
            .values(status=RunStatus.RUNNING.value, started_at=at)
        )
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(statement)

    async def append_step(
        self, user_id: uuid.UUID, run_id: uuid.UUID, step: NewStep, at: datetime
    ) -> StepRecord:
        async with user_transaction(self._sessions, user_id) as session:
            locked = await session.scalar(select(Run.id).where(Run.id == run_id).with_for_update())
            if locked is None:
                raise NotFound("Run not found.")
            return await _next_step(session, run_id, step, at)

    async def finish(
        self, user_id: uuid.UUID, run_id: uuid.UUID, finish: RunFinish, final: NewStep
    ) -> RunRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if row is None or row.status not in ACTIVE_STATUSES:
                return None
            row.status = finish.status.value
            row.outcome = finish.outcome
            row.finished_at = finish.at
            row.started_at = row.started_at or finish.at
            row.cost_usd = row.cost_usd + money(finish.cost_usd)
            row.model = finish.model or row.model
            row.provider = finish.provider or row.provider
            await session.flush()
            await _next_step(session, run_id, final, finish.at)
            return run_record(row)

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, after_seq: int, limit: int
    ) -> list[StepRecord]:
        query = (
            select(RunStep)
            .where(RunStep.run_id == run_id, RunStep.seq > after_seq)
            .order_by(RunStep.seq)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [step_record(row) for row in (await session.scalars(query)).all()]
