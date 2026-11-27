"""In-memory task chat store (offline dev and tests, ARCH-04). Rows are kept per `user_id`, as
row-level security keeps them in Postgres: one account never sees another's conversation."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from app.domain.devboard.proposals import ProposalRecord, ProposalStatus
from app.domain.ports.task_chat import TaskChatRecord


class MemoryTaskChats:
    def __init__(self) -> None:
        self.messages: dict[uuid.UUID, list[TaskChatRecord]] = {}
        self.proposals_by_user: dict[uuid.UUID, dict[uuid.UUID, ProposalRecord]] = {}

    def _messages(self, user_id: uuid.UUID, task_id: uuid.UUID) -> list[TaskChatRecord]:
        rows = [row for row in self.messages.get(user_id, []) if row.task_id == task_id]
        return sorted(rows, key=lambda row: (row.created_at, str(row.id)))

    async def list_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskChatRecord]:
        return self._messages(user_id, task_id)[offset : offset + limit]

    async def recent_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, limit: int
    ) -> list[TaskChatRecord]:
        return self._messages(user_id, task_id)[-limit:]

    async def add_message(self, user_id: uuid.UUID, message: TaskChatRecord) -> None:
        self.messages.setdefault(user_id, []).append(message)

    async def clear(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        kept = [row for row in self.messages.get(user_id, []) if row.task_id != task_id]
        self.messages[user_id] = kept
        mine = self.proposals_by_user.get(user_id, {})
        self.proposals_by_user[user_id] = {
            key: value for key, value in mine.items() if value.task_id != task_id
        }

    async def add_proposal(self, user_id: uuid.UUID, proposal: ProposalRecord) -> None:
        self.proposals_by_user.setdefault(user_id, {})[proposal.id] = proposal

    async def get_proposal(
        self, user_id: uuid.UUID, task_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> ProposalRecord | None:
        found = self.proposals_by_user.get(user_id, {}).get(proposal_id)
        return found if found is not None and found.task_id == task_id else None

    async def proposals(
        self, user_id: uuid.UUID, task_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ProposalRecord]:
        mine = self.proposals_by_user.get(user_id, {})
        return [mine[key] for key in ids if key in mine and mine[key].task_id == task_id]

    async def decide(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, status: ProposalStatus, at: datetime
    ) -> ProposalRecord | None:
        mine = self.proposals_by_user.get(user_id, {})
        current = mine.get(proposal_id)
        if current is None or current.status is not ProposalStatus.PENDING:
            return None
        decided = replace(current, status=status, decided_at=at)
        mine[proposal_id] = decided
        return decided
