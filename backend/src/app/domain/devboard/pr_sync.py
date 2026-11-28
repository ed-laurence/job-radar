"""PR sync (docs/features/github-prs.md, Flow step 6): bring GitHub's view of each pushed card's
PR onto the board.

A pass asks `PullRequests` about every live card with a branch in the pushed columns (at most
`MAX_CARDS_PER_PASS`, rotating through larger boards), and only then changes cards. So an
outage part-way changes nothing. Changes are system edits on the card as it is when applied:
the PR url, a `pr-closed` label once, and a merged card in human review moves to done. A card
that changed meanwhile (or moved on) is left for the next pass, so passes are idempotent and a
card is never moved twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import Enum, auto
from functools import partial

from app.domain.devboard.board import MAX_LABELS, BoardService
from app.domain.devboard.model import Actor, Column, TaskRecord
from app.domain.errors import Conflict, IllegalTransition, NotFound, UpstreamUnavailable
from app.domain.ports.devboard import (
    PullRequest,
    PullRequests,
    PullRequestState,
    TaskStore,
    github_branch,
)

MAX_CARDS_PER_PASS = 50
PR_CLOSED_LABEL = "pr-closed"
SYNC_COLUMNS = (
    Column.AGENT_DEV,
    Column.AGENT_REVIEW,
    Column.CHANGES_REQUESTED,
    Column.HUMAN_REVIEW,
)


@dataclass(frozen=True)
class PrSyncSummary:
    """What one pass did. `failure` is the safe reason a pass stopped (GitHub unavailable)."""

    checked: int = 0
    urls_recorded: int = 0
    moved_to_done: int = 0
    labelled: int = 0
    skipped: int = 0
    failure: str | None = None


class _Outcome(Enum):
    UNCHANGED = auto()
    URL = auto()
    LABELLED = auto()
    DONE = auto()
    SKIPPED = auto()


class PrSyncService:
    def __init__(self, tasks: TaskStore, board: BoardService, pull_requests: PullRequests) -> None:
        self._tasks = tasks
        self._board = board
        self._pull_requests = pull_requests
        self._offset = 0

    async def sync(self, actor: Actor) -> PrSyncSummary:
        """One pass, acting as the system for `actor`'s account (its RLS scope)."""
        system = actor.as_system()
        page = await self._tasks.list(
            system.user_id, SYNC_COLUMNS, self._offset, MAX_CARDS_PER_PASS
        )
        found: list[tuple[uuid.UUID, PullRequest | None]] = []
        try:
            for task in page:
                if task.branch:
                    found.append((task.id, await self._pull_requests.find(github_branch(task.id))))
        except UpstreamUnavailable as error:
            return PrSyncSummary(checked=len(found), failure=error.detail or "GitHub unavailable")
        self._offset = self._offset + len(page) if len(page) == MAX_CARDS_PER_PASS else 0
        outcomes = [
            await self._apply(system, task_id, pull_request)
            for task_id, pull_request in found
            if pull_request is not None
        ]
        return PrSyncSummary(
            checked=len(found),
            urls_recorded=outcomes.count(_Outcome.URL),
            moved_to_done=outcomes.count(_Outcome.DONE),
            labelled=outcomes.count(_Outcome.LABELLED),
            skipped=outcomes.count(_Outcome.SKIPPED),
        )

    async def _apply(
        self, system: Actor, task_id: uuid.UUID, pull_request: PullRequest
    ) -> _Outcome:
        try:
            task = await self._tasks.get(system.user_id, task_id)
            if task is None or task.column not in SYNC_COLUMNS:
                return _Outcome.SKIPPED
            if pull_request.state is PullRequestState.MERGED and task.column is Column.HUMAN_REVIEW:
                await self._board.system_move(
                    system, task_id, Column.DONE, change=partial(_with_url, url=pull_request.url)
                )
                return _Outcome.DONE
            saved = await self._board.system_edit(
                system, task_id, partial(_synced, pull_request=pull_request)
            )
        except (Conflict, NotFound, IllegalTransition):
            # The card changed while GitHub was asked; the next pass sees it as it is.
            return _Outcome.SKIPPED
        if saved is None:
            return _Outcome.UNCHANGED
        if PR_CLOSED_LABEL in saved.labels and PR_CLOSED_LABEL not in task.labels:
            return _Outcome.LABELLED
        return _Outcome.URL


def _with_url(task: TaskRecord, *, url: str) -> TaskRecord:
    return replace(task, pr_url=url)


def _synced(task: TaskRecord, *, pull_request: PullRequest) -> TaskRecord:
    synced = _with_url(task, url=pull_request.url)
    is_closed = pull_request.state is PullRequestState.CLOSED
    # The card's labels are capped by the contract; a full set goes without the label.
    if is_closed and PR_CLOSED_LABEL not in task.labels and len(task.labels) < MAX_LABELS:
        synced = replace(synced, labels=(*task.labels, PR_CLOSED_LABEL))
    return synced
