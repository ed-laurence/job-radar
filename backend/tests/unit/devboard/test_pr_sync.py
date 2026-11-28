"""PR sync (docs/features/github-prs.md, Flow step 6): the poller records each pushed card's PR
url, moves a merged card out of human review to done, labels a closed PR's card once, and
changes nothing when GitHub can't be asked."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.adapters.fakes.devboard import InMemoryTaskStore
from app.adapters.fakes.pull_requests import FakePullRequests
from app.adapters.fakes.security import FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.devboard.board import MAX_LABELS, BoardService
from app.domain.devboard.model import Actor, ActorKind, Column, EventKind, TaskRecord
from app.domain.devboard.pr_sync import MAX_CARDS_PER_PASS, PR_CLOSED_LABEL, PrSyncService
from app.domain.errors import UpstreamUnavailable
from app.domain.identity import AppRole
from app.domain.ports.devboard import PullRequest, PullRequestState, github_branch

SYSTEM = Actor(uuid.UUID(int=1), ActorKind.SYSTEM, AppRole.MAINTAINER)
REPO_URL = "https://github.com/acme/radar/pull/"
AT = datetime(2026, 1, 1, tzinfo=UTC)


class Harness:
    def __init__(self) -> None:
        self.store = InMemoryTaskStore()
        self.board = BoardService(self.store, FakePII(), FakeClock(), SequentialIds(start=1000))
        self.prs = FakePullRequests()
        self.sync = PrSyncService(self.store, self.board, self.prs)
        self._next = 0

    async def card(
        self,
        column: Column,
        *,
        branch: str | None = "agent/scratch",
        pr_url: str | None = None,
        labels: tuple[str, ...] = (),
    ) -> TaskRecord:
        self._next += 1
        task = TaskRecord(
            id=uuid.UUID(int=self._next),
            title=f"Card {self._next}",
            body="",
            column=column,
            rank=f"U{self._next:04d}",
            version=1,
            created_by=SYSTEM.user_id,
            created_at=AT,
            updated_at=AT,
            branch=branch,
            pr_url=pr_url,
            labels=labels,
        )
        await self.store.insert(SYSTEM.user_id, task)
        return task

    def pr(self, task: TaskRecord, state: PullRequestState, number: int = 7) -> PullRequest:
        found = PullRequest(url=f"{REPO_URL}{number}", number=number, state=state)
        self.prs.put(github_branch(task.id), found)
        return found

    def stored(self, task: TaskRecord) -> TaskRecord:
        return self.store.tasks[task.id]

    def events(self, task: TaskRecord) -> list[EventKind]:
        return [event.kind for event in self.store.events_log if event.task_id == task.id]


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.mark.parametrize(
    "column",
    [Column.AGENT_DEV, Column.AGENT_REVIEW, Column.CHANGES_REQUESTED, Column.HUMAN_REVIEW],
)
async def test_an_open_pr_records_its_url_only(harness: Harness, column: Column) -> None:
    task = await harness.card(column)
    found = harness.pr(task, PullRequestState.OPEN)

    summary = await harness.sync.sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.pr_url == found.url
    assert stored.column is column
    assert stored.labels == ()
    assert harness.events(task) == [EventKind.EDITED]
    assert summary.checked == 1
    assert summary.urls_recorded == 1
    assert summary.failure is None


async def test_each_card_is_asked_about_its_own_github_branch(harness: Harness) -> None:
    first = await harness.card(Column.AGENT_REVIEW)
    second = await harness.card(Column.HUMAN_REVIEW)

    await harness.sync.sync(SYSTEM)

    assert sorted(harness.prs.calls) == sorted([github_branch(first.id), github_branch(second.id)])


async def test_cards_without_a_branch_or_in_other_columns_are_not_asked(harness: Harness) -> None:
    await harness.card(Column.AGENT_DEV, branch=None)
    for column in (Column.BACKLOG, Column.READY, Column.BLOCKED, Column.DONE):
        await harness.card(column)

    summary = await harness.sync.sync(SYSTEM)

    assert harness.prs.calls == []
    assert summary.checked == 0


async def test_a_deleted_card_is_not_asked(harness: Harness) -> None:
    task = await harness.card(Column.HUMAN_REVIEW)
    harness.store.deleted[task.id] = AT

    await harness.sync.sync(SYSTEM)

    assert harness.prs.calls == []


async def test_no_pr_yet_changes_nothing(harness: Harness) -> None:
    task = await harness.card(Column.AGENT_REVIEW)

    summary = await harness.sync.sync(SYSTEM)

    assert harness.stored(task) == task
    assert harness.events(task) == []
    assert summary.checked == 1
    assert summary.urls_recorded == 0


async def test_an_unchanged_url_is_not_saved_again(harness: Harness) -> None:
    task = await harness.card(Column.AGENT_REVIEW, pr_url=f"{REPO_URL}7")
    harness.pr(task, PullRequestState.OPEN, number=7)

    summary = await harness.sync.sync(SYSTEM)

    assert harness.stored(task).version == 1
    assert harness.events(task) == []
    assert summary.urls_recorded == 0


async def test_a_new_pr_replaces_an_older_url(harness: Harness) -> None:
    task = await harness.card(Column.AGENT_REVIEW, pr_url=f"{REPO_URL}7")
    harness.pr(task, PullRequestState.OPEN, number=9)

    await harness.sync.sync(SYSTEM)

    assert harness.stored(task).pr_url == f"{REPO_URL}9"


async def test_a_url_update_is_a_system_edit(harness: Harness) -> None:
    task = await harness.card(Column.AGENT_REVIEW)
    harness.pr(task, PullRequestState.OPEN)

    await harness.sync.sync(SYSTEM)

    event = next(event for event in harness.store.events_log if event.task_id == task.id)
    assert event.kind is EventKind.EDITED
    assert event.actor_kind is ActorKind.SYSTEM


async def test_a_merged_pr_moves_a_human_review_card_to_done(harness: Harness) -> None:
    task = await harness.card(Column.HUMAN_REVIEW)
    found = harness.pr(task, PullRequestState.MERGED)

    summary = await harness.sync.sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.column is Column.DONE
    assert stored.pr_url == found.url
    move = next(event for event in harness.store.events_log if event.task_id == task.id)
    assert move.kind is EventKind.MOVED
    assert (move.from_column, move.to_column) == (Column.HUMAN_REVIEW, Column.DONE)
    assert move.actor_kind is ActorKind.SYSTEM
    assert summary.moved_to_done == 1


@pytest.mark.parametrize(
    "column", [Column.AGENT_DEV, Column.AGENT_REVIEW, Column.CHANGES_REQUESTED]
)
async def test_a_merged_pr_elsewhere_only_records_the_url(harness: Harness, column: Column) -> None:
    task = await harness.card(column)
    found = harness.pr(task, PullRequestState.MERGED)

    summary = await harness.sync.sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.column is column
    assert stored.pr_url == found.url
    assert stored.labels == ()
    assert summary.moved_to_done == 0


async def test_a_closed_pr_labels_the_card_once_and_leaves_it_in_place(harness: Harness) -> None:
    task = await harness.card(Column.HUMAN_REVIEW, labels=("ui",))
    found = harness.pr(task, PullRequestState.CLOSED)

    summary = await harness.sync.sync(SYSTEM)
    await harness.sync.sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.column is Column.HUMAN_REVIEW
    assert stored.labels == ("ui", PR_CLOSED_LABEL)
    assert stored.pr_url == found.url
    assert stored.version == 2
    assert summary.labelled == 1


async def test_an_outage_stops_the_pass_and_changes_no_card(harness: Harness) -> None:
    first = await harness.card(Column.AGENT_REVIEW)
    second = await harness.card(Column.HUMAN_REVIEW)
    harness.pr(first, PullRequestState.OPEN)
    harness.pr(second, PullRequestState.MERGED)
    harness.prs.outage = UpstreamUnavailable("gh is not signed in")

    summary = await harness.sync.sync(SYSTEM)

    assert harness.stored(first) == first
    assert harness.stored(second) == second
    assert harness.store.events_log == []
    assert summary.failure == "gh is not signed in"
    assert len(harness.prs.calls) == 1


class FailsOnSecondCall(FakePullRequests):
    async def find(self, branch: str) -> PullRequest | None:
        if len(self.calls) == 1:
            self.calls.append(branch)
            raise UpstreamUnavailable("gh pr list failed (exit 1)")
        return await super().find(branch)


async def test_an_outage_mid_pass_leaves_earlier_cards_unchanged(harness: Harness) -> None:
    harness.prs = FailsOnSecondCall()
    harness.sync = PrSyncService(harness.store, harness.board, harness.prs)
    first = await harness.card(Column.AGENT_REVIEW)
    second = await harness.card(Column.AGENT_REVIEW)
    harness.pr(first, PullRequestState.OPEN)
    harness.pr(second, PullRequestState.OPEN)

    summary = await harness.sync.sync(SYSTEM)

    assert harness.stored(first) == first
    assert harness.store.events_log == []
    assert summary.failure == "gh pr list failed (exit 1)"


async def test_passes_are_idempotent(harness: Harness) -> None:
    merged = await harness.card(Column.HUMAN_REVIEW)
    closed = await harness.card(Column.CHANGES_REQUESTED)
    opened = await harness.card(Column.AGENT_REVIEW)
    harness.pr(merged, PullRequestState.MERGED, number=1)
    harness.pr(closed, PullRequestState.CLOSED, number=2)
    harness.pr(opened, PullRequestState.OPEN, number=3)

    await harness.sync.sync(SYSTEM)
    after_first = {task_id: harness.store.tasks[task_id] for task_id in harness.store.tasks}
    events_after_first = len(harness.store.events_log)
    second = await harness.sync.sync(SYSTEM)

    assert harness.store.tasks == after_first
    assert len(harness.store.events_log) == events_after_first
    assert (second.urls_recorded, second.moved_to_done, second.labelled) == (0, 0, 0)


async def test_a_card_moved_by_one_pass_is_not_moved_by_a_racing_one(harness: Harness) -> None:
    task = await harness.card(Column.HUMAN_REVIEW)
    harness.pr(task, PullRequestState.MERGED)
    racing = PrSyncService(harness.store, harness.board, harness.prs)

    class MovesFirst(FakePullRequests):
        async def find(self, branch: str) -> PullRequest | None:
            # While this pass waits on GitHub, another pass finishes the same card.
            await racing.sync(SYSTEM)
            return harness.prs.by_branch.get(branch)

    late = PrSyncService(harness.store, harness.board, MovesFirst())
    summary = await late.sync(SYSTEM)

    moves = [event for event in harness.store.events_log if event.kind is EventKind.MOVED]
    assert len(moves) == 1
    assert harness.stored(task).column is Column.DONE
    assert summary.moved_to_done == 0


async def test_a_card_edited_during_the_pass_is_still_updated(harness: Harness) -> None:
    task = await harness.card(Column.AGENT_REVIEW)
    harness.pr(task, PullRequestState.OPEN)

    class EditsMeanwhile(FakePullRequests):
        async def find(self, branch: str) -> PullRequest | None:
            current = harness.store.tasks[task.id]
            harness.store.tasks[task.id] = replace(current, title="Edited", version=2)
            return harness.prs.by_branch.get(branch)

    await PrSyncService(harness.store, harness.board, EditsMeanwhile()).sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.title == "Edited"
    assert stored.pr_url == f"{REPO_URL}7"
    assert stored.version == 3


async def test_a_pass_asks_about_at_most_the_cap(harness: Harness) -> None:
    for _ in range(MAX_CARDS_PER_PASS + 5):
        await harness.card(Column.AGENT_REVIEW)

    summary = await harness.sync.sync(SYSTEM)

    assert MAX_CARDS_PER_PASS == 50
    assert len(harness.prs.calls) == MAX_CARDS_PER_PASS
    assert summary.checked == MAX_CARDS_PER_PASS


async def test_cards_past_the_cap_are_reached_by_the_next_pass(harness: Harness) -> None:
    cards = [await harness.card(Column.AGENT_REVIEW) for _ in range(MAX_CARDS_PER_PASS + 5)]

    await harness.sync.sync(SYSTEM)
    await harness.sync.sync(SYSTEM)

    asked = set(harness.prs.calls)
    assert asked == {github_branch(task.id) for task in cards}


async def test_a_card_with_a_full_label_set_gets_the_url_but_no_label(harness: Harness) -> None:
    full = tuple(f"label-{index}" for index in range(MAX_LABELS))
    task = await harness.card(Column.AGENT_REVIEW, labels=full)
    found = harness.pr(task, PullRequestState.CLOSED)

    await harness.sync.sync(SYSTEM)

    stored = harness.stored(task)
    assert stored.labels == full
    assert stored.pr_url == found.url
