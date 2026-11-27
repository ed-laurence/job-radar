"""Task chat in Postgres under the non-owner app role (FORCE RLS applies): each account's
conversation and proposals about a shared card are invisible to the other account."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_tasks import PgTaskStore
from app.adapters.db.engine import user_transaction
from app.adapters.db.task_chat import PgTaskChats
from app.domain.devboard.model import Column, TaskRecord
from app.domain.devboard.proposals import (
    CriteriaChange,
    EditChanges,
    ProposalRecord,
    ProposalStatus,
    TextChange,
)
from app.domain.ports.task_chat import Author, TaskChatRecord
from tests.integration.devboard.cleanup import leave_no_rows

pytestmark = pytest.mark.integration
__all__ = ["leave_no_rows"]  # the autouse fixture

type Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)


async def _card(sessions: Sessions, user: uuid.UUID) -> uuid.UUID:
    task = TaskRecord(uuid.uuid4(), "Card", "Body", Column.TRIAGED, "U", 1, user, NOW, NOW)
    await PgTaskStore(sessions).insert(user, task)
    return task.id


def _proposal(task_id: uuid.UUID) -> ProposalRecord:
    changes = EditChanges(
        title=TextChange("Card", "Better card"),
        acceptance_criteria=CriteriaChange((), ("shows a hint",)),
    )
    return ProposalRecord(
        uuid.uuid4(), task_id, ProposalStatus.PENDING, "Clearer.", changes, 1, NOW
    )


def _message(
    task_id: uuid.UUID, author: Author, at: datetime, proposal: uuid.UUID | None = None
) -> TaskChatRecord:
    return TaskChatRecord(uuid.uuid4(), task_id, author, f"{author} says", at, proposal)


async def test_the_tester_cannot_read_the_owners_task_chat(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    owner, tester = two_users
    task_id = await _card(sessions, owner)
    chats = PgTaskChats(sessions)
    proposal = _proposal(task_id)
    await chats.add_proposal(owner, proposal)
    await chats.add_message(owner, _message(task_id, "user", NOW))
    await chats.add_message(
        owner, _message(task_id, "assistant", NOW + timedelta(seconds=1), proposal.id)
    )

    assert await chats.list_messages(tester, task_id, 0, 50) == []
    assert await chats.recent_messages(tester, task_id, 40) == []
    assert await chats.get_proposal(tester, task_id, proposal.id) is None
    assert await chats.proposals(tester, task_id, [proposal.id]) == []
    assert await chats.decide(tester, proposal.id, ProposalStatus.ACCEPTED, NOW) is None
    await chats.clear(tester, task_id)  # deletes nothing of the owner's

    mine = await chats.list_messages(owner, task_id, 0, 50)
    assert [message.author for message in mine] == ["user", "assistant"]
    assert mine[1].proposal_id == proposal.id
    stored = await chats.get_proposal(owner, task_id, proposal.id)
    assert stored == proposal


async def test_the_tester_cannot_write_into_the_owners_chat(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    owner, tester = two_users
    task_id = await _card(sessions, owner)
    insert = text(
        "INSERT INTO task_chat_messages (user_id, task_id, author, content)"
        " VALUES (:owner, :task, 'user', 'hi')"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_transaction(sessions, tester) as session:
            await session.execute(insert, {"owner": owner, "task": task_id})


async def test_a_proposal_is_decided_once_and_cleared_with_the_chat(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    owner, _ = two_users
    task_id = await _card(sessions, owner)
    chats = PgTaskChats(sessions)
    proposal = _proposal(task_id)
    await chats.add_proposal(owner, proposal)
    await chats.add_message(owner, _message(task_id, "assistant", NOW, proposal.id))
    decided = await chats.decide(owner, proposal.id, ProposalStatus.REJECTED, NOW)
    assert decided is not None
    assert (decided.status, decided.decided_at) == (ProposalStatus.REJECTED, NOW)
    assert await chats.decide(owner, proposal.id, ProposalStatus.ACCEPTED, NOW) is None
    await chats.clear(owner, task_id)
    assert await chats.list_messages(owner, task_id, 0, 50) == []
    assert await chats.get_proposal(owner, task_id, proposal.id) is None
