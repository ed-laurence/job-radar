"""Postgres adapter for task chat. Both tables are user-owned: every statement runs in
`user_transaction`, so one account never sees another's conversation or proposals (SEC-03)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_task_chat import TaskChatMessage, TaskChatProposal
from app.domain.devboard.proposals import (
    ProposalRecord,
    ProposalStatus,
    changes_from_dict,
    changes_to_dict,
)
from app.domain.ports.task_chat import Author, TaskChatRecord

type Sessions = async_sessionmaker[AsyncSession]


def _proposal(row: TaskChatProposal) -> ProposalRecord:
    return ProposalRecord(
        id=row.id,
        task_id=row.task_id,
        status=ProposalStatus(row.status),
        reason=row.reason,
        changes=changes_from_dict(row.changes),
        base_version=row.base_version,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


def _message(row: TaskChatMessage) -> TaskChatRecord:
    return TaskChatRecord(
        id=row.id,
        task_id=row.task_id,
        author=cast("Author", row.author),
        content=row.content,
        created_at=row.created_at,
        proposal_id=row.proposal_id,
    )


class PgTaskChats:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def list_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskChatRecord]:
        stmt = (
            select(TaskChatMessage)
            .where(TaskChatMessage.task_id == task_id)
            .order_by(TaskChatMessage.created_at, TaskChatMessage.id)
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [_message(row) for row in await session.scalars(stmt)]

    async def recent_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, limit: int
    ) -> list[TaskChatRecord]:
        stmt = (
            select(TaskChatMessage)
            .where(TaskChatMessage.task_id == task_id)
            .order_by(TaskChatMessage.created_at.desc(), TaskChatMessage.id.desc())
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = [_message(row) for row in await session.scalars(stmt)]
        return rows[::-1]

    async def add_message(self, user_id: uuid.UUID, message: TaskChatRecord) -> None:
        row = TaskChatMessage(
            id=message.id,
            user_id=user_id,
            task_id=message.task_id,
            author=message.author,
            content=message.content,
            proposal_id=message.proposal_id,
            created_at=message.created_at,
        )
        async with user_transaction(self._sessions, user_id) as session:
            session.add(row)

    async def clear(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(delete(TaskChatMessage).where(TaskChatMessage.task_id == task_id))
            await session.execute(
                delete(TaskChatProposal).where(TaskChatProposal.task_id == task_id)
            )

    async def add_proposal(self, user_id: uuid.UUID, proposal: ProposalRecord) -> None:
        row = TaskChatProposal(
            id=proposal.id,
            user_id=user_id,
            task_id=proposal.task_id,
            status=proposal.status.value,
            reason=proposal.reason,
            changes=changes_to_dict(proposal.changes),
            base_version=proposal.base_version,
            created_at=proposal.created_at,
            decided_at=proposal.decided_at,
        )
        async with user_transaction(self._sessions, user_id) as session:
            session.add(row)

    async def get_proposal(
        self, user_id: uuid.UUID, task_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> ProposalRecord | None:
        stmt = select(TaskChatProposal).where(
            TaskChatProposal.id == proposal_id, TaskChatProposal.task_id == task_id
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(stmt)
        return None if row is None else _proposal(row)

    async def proposals(
        self, user_id: uuid.UUID, task_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ProposalRecord]:
        stmt = select(TaskChatProposal).where(
            TaskChatProposal.task_id == task_id, TaskChatProposal.id.in_(list(ids))
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [_proposal(row) for row in await session.scalars(stmt)]

    async def decide(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, status: ProposalStatus, at: datetime
    ) -> ProposalRecord | None:
        stmt = (
            update(TaskChatProposal)
            .where(
                TaskChatProposal.id == proposal_id,
                TaskChatProposal.status == ProposalStatus.PENDING.value,
            )
            .values(status=status.value, decided_at=at)
            .returning(TaskChatProposal)
        )
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(stmt)
        return None if row is None else _proposal(row)
