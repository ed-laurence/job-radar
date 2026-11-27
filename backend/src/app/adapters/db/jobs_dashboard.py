"""Postgres adapter for the dashboard's read-only facts (under `user_transaction`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.adapters.db.engine import user_transaction
from app.adapters.db.jobs_mapping import Sessions, to_record
from app.adapters.db.tables_board import DevTask, Run, TaskEvent, TaskPlan, TaskTriage
from app.adapters.db.tables_jobs import (
    Rejection,
    Role,
    RoleEvent,
)
from app.adapters.db.tables_platform import LlmCall
from app.domain.jobs.ports import (
    BoardFacts,
    CallFact,
    ColumnEntry,
    DevBoardFacts,
    RejectionFact,
    ReviewFact,
    RoleFact,
    RunFact,
    StatusFact,
    TaskFact,
    TriageFact,
)


class PgDashboard:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def roles(self, user_id: uuid.UUID) -> list[RoleFact]:
        stmt = select(Role).where(Role.user_id == user_id, Role.deleted_at.is_(None))
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(RoleFact, row) for row in await session.scalars(stmt)]

    async def status_events(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[StatusFact]:
        stmt = select(RoleEvent.role_id, RoleEvent.at, RoleEvent.to_value).where(
            RoleEvent.kind == "status",
            RoleEvent.to_value.is_not(None),
            RoleEvent.at >= start,
            RoleEvent.at < end,
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.execute(stmt)
            return [StatusFact(role_id=r[0], at=r[1], to_value=str(r[2])) for r in rows]

    async def rejections(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[RejectionFact]:
        stmt = select(Rejection.rejected_at, Rejection.reason_categories).where(
            Rejection.rejected_at >= start, Rejection.rejected_at < end
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.execute(stmt)
            return [
                RejectionFact(at=at, categories=tuple(categories or ()))
                for at, categories in rows
                if at is not None
            ]

    async def llm_calls(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[CallFact]:
        stmt = select(LlmCall).where(LlmCall.at >= start, LlmCall.at < end)
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(CallFact, row) for row in await session.scalars(stmt)]

    async def board(self, user_id: uuid.UUID, end: datetime) -> BoardFacts:
        latest_mode = (
            select(TaskPlan.mode)
            .where(TaskPlan.task_id == Run.task_id)
            .order_by(TaskPlan.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        reviews = select(Run.finished_at, func.coalesce(latest_mode, "single"), Run.outcome).where(
            Run.kind == "review", Run.round == 0, Run.finished_at.is_not(None)
        )
        entries = (
            select(TaskEvent.task_id, TaskEvent.at, TaskEvent.to_column)
            .join(DevTask, DevTask.id == TaskEvent.task_id)
            .where(
                TaskEvent.to_column.is_not(None), TaskEvent.at < end, DevTask.deleted_at.is_(None)
            )
        )
        columns = select(DevTask.column).where(DevTask.deleted_at.is_(None))
        async with user_transaction(self._sessions, user_id) as session:
            entry_rows = (await session.execute(entries)).all()
            review_rows = (await session.execute(reviews)).all()
            live = (await session.scalars(columns)).all()
        return BoardFacts(
            entries=tuple(ColumnEntry(task_id=r[0], at=r[1], column=str(r[2])) for r in entry_rows),
            reviews=tuple(
                ReviewFact(at=at, mode=str(mode), is_passed=outcome == "approved")
                for at, mode, outcome in review_rows
                if at is not None
            ),
            live_columns=tuple(live),
        )

    async def dev_board(self, user_id: uuid.UUID) -> DevBoardFacts:
        live = DevTask.deleted_at.is_(None)
        tasks = select(DevTask.id, DevTask.column, DevTask.created_at).where(live)
        entries = (
            select(TaskEvent.task_id, TaskEvent.at, TaskEvent.to_column)
            .join(DevTask, DevTask.id == TaskEvent.task_id)
            .where(TaskEvent.to_column.is_not(None), live)
        )
        runs = select(
            Run.task_id,
            Run.kind,
            Run.status,
            Run.round,
            Run.cost_usd,
            Run.created_at,
            Run.outcome,
            Run.finished_at,
        )
        triage = select(TaskTriage.task_id, TaskTriage.created_at, TaskTriage.in_scope)
        async with user_transaction(self._sessions, user_id) as session:
            task_rows = (await session.execute(tasks)).all()
            entry_rows = (await session.execute(entries)).all()
            run_rows = (await session.execute(runs)).all()
            triage_rows = (await session.execute(triage)).all()
        return DevBoardFacts(
            tasks=tuple(TaskFact(id=r[0], column=str(r[1]), created_at=r[2]) for r in task_rows),
            entries=tuple(ColumnEntry(task_id=r[0], at=r[1], column=str(r[2])) for r in entry_rows),
            runs=tuple(
                RunFact(
                    task_id=r[0],
                    kind=str(r[1]),
                    status=str(r[2]),
                    round=int(r[3]),
                    cost_usd=float(r[4]),
                    created_at=r[5],
                    outcome=r[6],
                    finished_at=r[7],
                )
                for r in run_rows
            ),
            triage=tuple(
                TriageFact(task_id=r[0], at=r[1], in_scope=bool(r[2])) for r in triage_rows
            ),
        )
