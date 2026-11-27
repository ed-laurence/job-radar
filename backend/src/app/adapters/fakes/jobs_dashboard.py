"""In-memory `DashboardSource` fake (ARCH-04): reads the in-memory role store."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from app.adapters.fakes.jobs import MemoryRoles, build
from app.domain.jobs.ports import (
    BoardFacts,
    CallFact,
    DevBoardFacts,
    RejectionFact,
    RoleFact,
    StatusFact,
)


class MemoryDashboard:
    """Reads the in-memory role store; LLM calls come from a callable supplied by composition."""

    def __init__(
        self, roles: MemoryRoles, calls: Callable[[uuid.UUID], Sequence[CallFact]] | None = None
    ) -> None:
        self._roles = roles
        self._calls = calls
        self.dev_board_facts = DevBoardFacts()

    async def roles(self, user_id: uuid.UUID) -> list[RoleFact]:
        return [
            build(RoleFact, dataclasses.asdict(role))
            for (owner, _), role in self._roles.roles.items()
            if owner == user_id and role.deleted_at is None
        ]

    async def status_events(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[StatusFact]:
        return [
            StatusFact(role_id=event.role_id, at=event.at, to_value=event.to_value)
            for (owner, _), event in self._roles.events.items()
            if owner == user_id
            and event.kind == "status"
            and event.to_value is not None
            and start <= event.at < end
        ]

    async def rejections(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[RejectionFact]:
        return [
            RejectionFact(at=row.rejected_at, categories=row.reason_categories)
            for (owner, _), row in self._roles.rejections.items()
            if owner == user_id and row.rejected_at is not None and start <= row.rejected_at < end
        ]

    async def llm_calls(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[CallFact]:
        calls: Sequence[CallFact] = self._calls(user_id) if self._calls is not None else ()
        return [call for call in calls if start <= call.at < end]

    async def board(self, user_id: uuid.UUID, end: datetime) -> BoardFacts:
        del user_id, end  # The board has no in-memory store here: no facts.
        return BoardFacts()

    async def dev_board(self, user_id: uuid.UUID) -> DevBoardFacts:
        del user_id  # The board is shared: every signed-in user sees the same facts.
        return self.dev_board_facts
