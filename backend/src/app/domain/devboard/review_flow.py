"""Agent review and human review notes (docs/features/agent-review.md sections 2-4).

`ReviewFlow` turns a review run's result into a stored `ReviewRecord` and the card's next
column; the `Workflow` launches the runs. A review run that fails, or returns no review,
blocks the card with the reason (the run keeps its spend).

A human's "Request changes" notes are stored as a review too, so the rework run gets them the
same way as an agent's blockers: one BLOCKER whose `evidence` and `repro` are the notes, with
PII replaced first (as card text is), so neither the stored review nor the rework prompt
holds raw PII. Its
`path` is `""` because the notes are about the change as a whole, not one file (the only
place an empty path is stored). Its round is the card's current round: a human round
doesn't count towards the agent review cap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.domain.devboard.board import BoardService
from app.domain.devboard.model import (
    Actor,
    Column,
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
    RunRecord,
    TaskRecord,
)
from app.domain.devboard.review_policy import MAX_TEXT, clean_summary, diff_paths, finalise
from app.domain.errors import DomainError
from app.domain.ports import Clock, Ids
from app.domain.ports.devboard import (
    AgentResult,
    BranchViews,
    ReviewDraft,
    RubricIds,
    TaskStore,
)

NEEDS_DECISION = "needs-decision"
NO_REVIEW = "The review run returned no review."
HUMAN_SUMMARY = "Changes requested in human review."


@dataclass(frozen=True)
class ReviewOutcome:
    """How the review run finishes, and the verdict when the card moved on it."""

    result: AgentResult
    verdict: ReviewVerdict | None


@dataclass(frozen=True)
class ReviewInputs:
    """`branches=None`: no code host, so findings aren't checked against the changed files.
    `rubric_ids=None`: no rubric, so a BLOCKER must cite a test or a repro."""

    branches: BranchViews | None = None
    rubric_ids: RubricIds | None = None


class ReviewFlow:
    def __init__(
        self,
        board: BoardService,
        tasks: TaskStore,
        inputs: ReviewInputs,
        system: tuple[Clock, Ids],
    ) -> None:
        self._board, self._tasks = board, tasks
        self._branches, self._rubric = inputs.branches, inputs.rubric_ids
        self._clock, self._ids = system

    async def conclude(self, actor: Actor, run: RunRecord, result: AgentResult) -> ReviewOutcome:
        if not result.is_success:
            return await self._fail(actor, run, result, result.outcome)
        if result.review is None:
            return await self._fail(actor, run, result, NO_REVIEW)
        try:
            return await self._apply(actor, run, result, result.review)
        except DomainError as error:
            reason = error.detail or "The review couldn't be checked."
            return await self._fail(actor, run, result, reason)

    async def add_notes(
        self, actor: Actor, task: TaskRecord, run: RunRecord, notes: str
    ) -> ReviewRecord:
        clean = (await self._board.without_pii(notes.strip()))[:MAX_TEXT]
        blocker = ReviewFinding(ReviewSeverity.BLOCKER, path="", evidence=clean, repro=clean)
        record = ReviewRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            run_id=run.id,
            round=max(task.review_round, 1),
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            summary=HUMAN_SUMMARY,
            findings=(blocker,),
            created_at=self._clock.now(),
        )
        await self._tasks.add_review(actor.user_id, record)
        return record

    async def _apply(
        self, actor: Actor, run: RunRecord, result: AgentResult, draft: ReviewDraft
    ) -> ReviewOutcome:
        task = await self._board.get(actor, run.task_id)
        review_round = task.review_round + 1
        findings, verdict = finalise(
            draft,
            review_round=review_round,
            rubric_ids=self._rubric.ids() if self._rubric else frozenset(),
            changed_paths=await self._changed_paths(task),
        )
        record = ReviewRecord(
            id=self._ids.new_id(),
            task_id=task.id,
            run_id=run.id,
            round=review_round,
            verdict=verdict,
            summary=clean_summary(draft.summary),
            findings=findings,
            created_at=self._clock.now(),
            model=result.model,
        )
        await self._tasks.add_review(actor.user_id, record)
        finished = replace(result, outcome=_outcome(record))
        # Like a dev run: the card moves only if it is still where the run left it.
        current = await self._board.get(actor, run.task_id)
        if current.column is not Column.AGENT_REVIEW:
            return ReviewOutcome(finished, None)
        target = (
            Column.CHANGES_REQUESTED
            if verdict is ReviewVerdict.CHANGES_REQUESTED
            else Column.HUMAN_REVIEW
        )
        await self._board.system_move(
            actor.as_agent(),
            task.id,
            target,
            run_id=run.id,
            change=lambda card: _reviewed(card, review_round, verdict),
        )
        return ReviewOutcome(finished, verdict)

    async def _changed_paths(self, task: TaskRecord) -> frozenset[str] | None:
        if self._branches is None or not task.branch or not task.base_sha:
            return None
        found = await self._branches.diff(task.base_sha, task.branch)
        return diff_paths(found.text)

    async def _fail(
        self, actor: Actor, run: RunRecord, result: AgentResult, reason: str
    ) -> ReviewOutcome:
        """The run fails with `reason` and keeps the result's spend."""
        task = await self._tasks.get(actor.user_id, run.task_id)
        if task is not None and task.column is Column.AGENT_REVIEW:
            await self._board.system_move(
                actor.as_agent(), run.task_id, Column.BLOCKED, reason=reason, run_id=run.id
            )
        return ReviewOutcome(replace(result, is_success=False, outcome=reason), None)


def without_needs_decision(task: TaskRecord) -> TaskRecord:
    return replace(task, labels=tuple(label for label in task.labels if label != NEEDS_DECISION))


def _reviewed(task: TaskRecord, review_round: int, verdict: ReviewVerdict) -> TaskRecord:
    labels = task.labels
    if verdict is ReviewVerdict.NEEDS_DECISION and NEEDS_DECISION not in labels:
        labels = (*labels, NEEDS_DECISION)
    return replace(task, review_round=review_round, labels=labels)


def _outcome(record: ReviewRecord) -> str:
    blockers = len(record.blockers)
    nits = len(record.findings) - blockers
    counts = f"{blockers} blockers, {nits} nits"
    return f"Review round {record.round}: {record.verdict.value} ({counts})."
