"""Accepting or rejecting a task-chat proposal (spec `acceptTaskChatProposal`,
`rejectTaskChatProposal`).

Accepting is the normal task update (`BoardService.update`: version check, PII redaction, an
`edited` event, re-triage for Backlog/Triaged/Out of scope). On a card past triage the edit
makes the triage and the plan approval stale, and the state machine had no rule for that, so
`state_machine.edit_returns_to_backlog` adds one: the plan approval is withdrawn, the card goes
back to Backlog with a note in the chat, and triage runs again. Done cards can't be edited, and
nothing is applied while an agent run is active on the card.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.devboard.board import BoardService
from app.domain.devboard.model import Actor, TaskRecord
from app.domain.devboard.plans import PlanService
from app.domain.devboard.proposals import (
    ProposalRecord,
    ProposalStatus,
    check_fresh,
    to_task_edit,
)
from app.domain.devboard.state_machine import edit_returns_to_backlog
from app.domain.devboard.workflow import Workflow
from app.domain.errors import Conflict, NotFound
from app.domain.ports import Clock, Ids
from app.domain.ports.task_chat import TaskChatRecord, TaskChatStore

RETURNED_NOTE = (
    "Edit accepted. The card was past triage, so it went back to Backlog: triage runs again, "
    "and its tests and plan need approving again before it can be Ready."
)
RUN_ACTIVE = (
    "An agent run is active on this card. Wait for it to finish, or stop it with the kill "
    "switch, before accepting an edit."
)


@dataclass(frozen=True)
class AcceptOutcome:
    proposal: ProposalRecord
    task: TaskRecord
    returned_to_backlog: bool
    note: str | None = None


class ProposalDecisions:
    def __init__(
        self,
        services: tuple[BoardService, PlanService, Workflow],
        store: TaskChatStore,
        system: tuple[Clock, Ids],
    ) -> None:
        self._board, self._plans, self._workflow = services
        self._store = store
        self._clock, self._ids = system

    async def accept(
        self, actor: Actor, task_id: uuid.UUID, proposal_id: uuid.UUID, version: int
    ) -> AcceptOutcome:
        task = await self._board.get(actor, task_id)
        proposal = await self._pending(actor, task_id, proposal_id)
        if task.active_run_id is not None:
            raise Conflict(RUN_ACTIVE)
        if task.version != version:
            raise Conflict("The card changed elsewhere.", current_version=task.version)
        returns = edit_returns_to_backlog(task)
        check_fresh(task, proposal.changes)
        await self._board.update(actor, task_id, to_task_edit(task, proposal.changes, version))
        note = None
        if returns:
            await self._plans.withdraw_approval(actor, task_id)
            await self._board.return_to_backlog(actor.as_system(), task_id)
            await self._workflow.queue_triage(actor, task_id)
            note = RETURNED_NOTE
            await self._note(actor, task_id, note)
        decided = await self._decide(actor, proposal, ProposalStatus.ACCEPTED)
        current = await self._board.get(actor, task_id)
        return AcceptOutcome(decided, current, returned_to_backlog=returns, note=note)

    async def reject(
        self, actor: Actor, task_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> ProposalRecord:
        await self._board.get(actor, task_id)
        proposal = await self._pending(actor, task_id, proposal_id)
        return await self._decide(actor, proposal, ProposalStatus.REJECTED)

    async def _pending(
        self, actor: Actor, task_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> ProposalRecord:
        proposal = await self._store.get_proposal(actor.user_id, task_id, proposal_id)
        if proposal is None:
            raise NotFound("Proposal not found.")
        if proposal.status is not ProposalStatus.PENDING:
            raise Conflict(f"This proposal was already {proposal.status.value}.")
        return proposal

    async def _decide(
        self, actor: Actor, proposal: ProposalRecord, status: ProposalStatus
    ) -> ProposalRecord:
        decided = await self._store.decide(actor.user_id, proposal.id, status, self._clock.now())
        if decided is None:
            raise Conflict("This proposal was decided elsewhere.")
        return decided

    async def _note(self, actor: Actor, task_id: uuid.UUID, text: str) -> None:
        note = TaskChatRecord(
            id=self._ids.new_id(),
            task_id=task_id,
            author="assistant",
            content=text,
            created_at=self._clock.now(),
        )
        await self._store.add_message(actor.user_id, note)
