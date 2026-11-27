"""Work plans and the Plan review gate.

The acceptance tests are frozen by the test-only step and a human approves them together
with the plan; only then can the card reach `ready`. Plan risk comes from the path-risk map
over every subtask's lease paths, and the planner's own estimate can only raise it.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.domain.devboard.board import BoardService
from app.domain.devboard.model import (
    Actor,
    Column,
    EventKind,
    PlanRecord,
    Risk,
    TaskRecord,
    max_risk,
)
from app.domain.devboard.paths import InvalidPath, code_risk, normalise
from app.domain.errors import Conflict, IllegalTransition, NotFound, PlanNotApproved
from app.domain.ports import Clock, Ids
from app.domain.ports.devboard import PlanDraft, TaskStore


def plan_risk(draft: PlanDraft) -> Risk:
    paths: list[str] = []
    for subtask in draft.subtasks:
        for raw in subtask.lease_paths:
            try:
                paths.append(normalise(raw))
            except InvalidPath:
                # An unusable lease path can't be judged safe.
                return Risk.HIGH
    return max_risk(code_risk(tuple(paths)), draft.llm_risk)


class PlanService:
    def __init__(self, store: TaskStore, board: BoardService, clock: Clock, ids: Ids) -> None:
        self._store = store
        self._board = board
        self._clock = clock
        self._ids = ids

    async def get(self, actor: Actor, task_id: uuid.UUID) -> PlanRecord:
        await self._board.get(actor, task_id)
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None:
            raise NotFound("This card has no plan yet.")
        return plan

    async def latest(self, actor: Actor, task_id: uuid.UUID) -> PlanRecord | None:
        await self._board.get(actor, task_id)
        return await self._store.latest_plan(actor.user_id, task_id)

    async def save_draft(self, actor: Actor, task: TaskRecord, draft: PlanDraft) -> PlanRecord:
        previous = await self._store.latest_plan(actor.user_id, task.id)
        plan = PlanRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            version=1 if previous is None else previous.version + 1,
            mode=draft.mode,
            subtasks=draft.subtasks,
            acceptance_tests=draft.acceptance_tests,
            budget_usd=draft.budget_usd,
            budget_minutes=draft.budget_minutes,
            risk=plan_risk(draft),
            created_at=self._clock.now(),
        )
        await self._store.add_plan(actor.user_id, plan)
        await self._board.record(actor, task, EventKind.PLANNED)
        return plan

    async def withdraw_approval(self, actor: Actor, task_id: uuid.UUID) -> None:
        """The card's text changed after approval: tests and plan must be approved again."""
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None or (plan.tests_approved_at is None and plan.plan_approved_at is None):
            return
        withdrawn = replace(plan, tests_approved_at=None, plan_approved_at=None, approved_by=None)
        await self._store.save_plan(actor.user_id, withdrawn, plan.version)

    async def approve(
        self,
        actor: Actor,
        task_id: uuid.UUID,
        *,
        plan_version: int,
        task_version: int,
        rank: str | None,
    ) -> TaskRecord:
        task = await self._board.get(actor, task_id)
        if task.column is not Column.PLAN_REVIEW:
            raise IllegalTransition(
                "Only cards in Plan review can be approved.", allowed=(task.column.value,)
            )
        plan = await self._store.latest_plan(actor.user_id, task_id)
        if plan is None:
            raise PlanNotApproved("There is no plan to approve yet.")
        if task.version != task_version:
            raise Conflict("The card changed elsewhere.", current_version=task.version)
        if plan.version != plan_version:
            raise Conflict("The plan changed elsewhere.", current_version=plan.version)
        now = self._clock.now()
        approved = replace(
            plan, tests_approved_at=now, plan_approved_at=now, approved_by=actor.user_id
        )
        await self._store.save_plan(actor.user_id, approved, plan_version)
        moved = await self._board.system_move(actor, task_id, Column.READY, rank=rank)
        await self._board.record(actor, moved, EventKind.APPROVED)
        return moved
