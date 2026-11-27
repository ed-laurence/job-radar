"""The task-chat agent's tools, bound by the domain to one card and one account. They read
that card only, and `propose` stores a pending proposal: nothing here edits a card."""

from __future__ import annotations

import uuid

from app.domain.devboard.board import BoardService, redact_pii
from app.domain.devboard.model import Actor, PlanRecord, TaskRecord, TriageRecord
from app.domain.devboard.plans import PlanService
from app.domain.devboard.proposals import (
    EditChanges,
    ProposalRecord,
    ProposalStatus,
    ProposedEdit,
    diff_edit,
)
from app.domain.errors import ValidationFailed
from app.domain.ports import PII, Clock, Ids
from app.domain.ports.task_chat import TaskChatStore


class BoundTools:
    """One per reply. At most one proposal per reply, so a message shows at most one."""

    def __init__(
        self,
        services: tuple[BoardService, PlanService],
        store: TaskChatStore,
        pii: PII,
        system: tuple[Clock, Ids],
        scope: tuple[Actor, uuid.UUID],
    ) -> None:
        self._board, self._plans = services
        self._store = store
        self._pii = pii
        self._clock, self._ids = system
        self._actor, self._task_id = scope
        self.proposal: ProposalRecord | None = None

    async def task(self) -> TaskRecord:
        return await self._board.get(self._actor, self._task_id)

    async def triage(self) -> TriageRecord | None:
        return await self._board.latest_triage(self._actor, self._task_id)

    async def plan(self) -> PlanRecord | None:
        return await self._plans.latest(self._actor, self._task_id)

    async def propose(self, edit: ProposedEdit) -> EditChanges:
        if self.proposal is not None:
            raise ValidationFailed("You already proposed an edit in this reply.")
        task = await self.task()
        # Proposed text is shown and may be saved: PII is redacted as on create and edit.
        redacted = await self._redacted(edit)
        changes = diff_edit(task, redacted)
        proposal = ProposalRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            status=ProposalStatus.PENDING,
            reason=redacted.reason.strip(),
            changes=changes,
            base_version=task.version,
            created_at=self._clock.now(),
        )
        await self._store.add_proposal(self._actor.user_id, proposal)
        self.proposal = proposal
        return changes

    async def _redacted(self, edit: ProposedEdit) -> ProposedEdit:
        async def clean(text: str | None) -> str | None:
            return None if text is None else (await redact_pii(self._pii, text))[0]

        criteria = edit.acceptance_criteria
        return ProposedEdit(
            reason=(await redact_pii(self._pii, edit.reason))[0],
            title=await clean(edit.title),
            description=await clean(edit.description),
            acceptance_criteria=None
            if criteria is None
            else tuple([(await redact_pii(self._pii, item))[0] for item in criteria]),
        )
