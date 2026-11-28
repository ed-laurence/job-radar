"""The flow after a dev run: agent review, rework on blockers, the round cap, and a human's
request for changes (docs/features/agent-review.md sections 2-4)."""

from __future__ import annotations

import pytest

from app.api.devboard import mapping
from app.domain.devboard.model import (
    Actor,
    Column,
    MoveRequest,
    ReviewSeverity,
    ReviewVerdict,
    RunKind,
    RunStatus,
    TaskRecord,
)
from app.domain.devboard.review_flow import NEEDS_DECISION, NO_REVIEW
from app.domain.errors import Forbidden, ValidationFailed
from app.domain.ports.devboard import AgentResult, github_branch
from tests.unit.devboard.workflow_harness import (
    BASE_SHA,
    MAINTAINER,
    USER,
    Harness,
    blocker,
    branch_name,
    reviewed,
)

DEV, REVIEW, TRIAGE = RunKind.DEV, RunKind.REVIEW, RunKind.TRIAGE


async def _human_review(harness: Harness) -> TaskRecord:
    task = await harness.start_dev()
    assert task.column is Column.HUMAN_REVIEW
    return task


async def _request_changes(
    harness: Harness, task: TaskRecord, notes: str | None, actor: Actor = MAINTAINER
) -> TaskRecord:
    move = MoveRequest(Column.CHANGES_REQUESTED, "U", task.version, reason=notes)
    moved = await harness.board.move(actor, task.id, move)
    await harness.dispatcher.drain()
    return await harness.board.get(MAINTAINER, moved.id)


async def test_a_dev_run_stores_its_branch_and_starts_the_review() -> None:
    harness = Harness()
    task = await harness.start_dev()
    dev = await harness.latest_run(task, DEV)
    assert task.branch == branch_name(dev)
    assert task.base_sha == BASE_SHA
    assert task.pr_branch is None
    assert await harness.kinds(task) == [TRIAGE, DEV, REVIEW]
    review_run = await harness.latest_run(task, REVIEW)
    assert review_run.round == 1
    assert review_run.agent == "reviewer"
    context = harness.runner.seen[-1]
    assert context.run.kind is REVIEW
    assert context.review is None


async def test_a_pushed_dev_run_stores_its_github_branch() -> None:
    harness = Harness()
    harness.runner.pushes = True
    task = await harness.start_dev()
    dev = await harness.latest_run(task, DEV)
    assert task.pr_branch == github_branch(task.id, dev.id)


async def test_a_rework_keeps_the_cards_github_branch() -> None:
    harness = Harness()
    harness.runner.pushes = True
    harness.runner.reviews = [reviewed(blocker())]
    task = await harness.start_dev()
    runs = await harness.runs.for_task(MAINTAINER.user_id, task.id, 0, 20)
    dev_runs = [run for run in reversed(runs) if run.kind is DEV]
    assert len(dev_runs) == 2
    assert task.pr_branch == github_branch(task.id, dev_runs[0].id)


async def test_a_clean_review_moves_the_card_to_human_review() -> None:
    harness = Harness()
    task = await harness.start_dev()
    assert task.column is Column.HUMAN_REVIEW
    assert task.review_round == 1
    review = await harness.tasks.latest_review(MAINTAINER.user_id, task.id)
    assert review is not None
    run = await harness.latest_run(task, REVIEW)
    assert (review.round, review.verdict, review.run_id) == (1, ReviewVerdict.APPROVED, run.id)
    assert run.status is RunStatus.SUCCEEDED
    assert run.cost_usd == 0.75


async def test_blockers_in_round_one_start_a_rework_run_with_them() -> None:
    harness = Harness()
    harness.runner.reviews.append(reviewed(blocker()))
    task = await harness.start_dev()
    assert await harness.kinds(task) == [TRIAGE, DEV, REVIEW, DEV, REVIEW]
    first = next(c for c in harness.runner.seen if c.run.kind is DEV and c.run.round == 1)
    assert first.review is not None
    assert first.review.verdict is ReviewVerdict.CHANGES_REQUESTED
    assert first.review.blockers[0].rubric_id == "ARCH-03"
    # The second (clean) review approves: round 2, human review, no decision label.
    assert task.column is Column.HUMAN_REVIEW
    assert task.review_round == 2
    assert NEEDS_DECISION not in task.labels


async def test_blockers_again_in_round_two_need_a_human_decision() -> None:
    harness = Harness()
    harness.runner.reviews.extend([reviewed(blocker()), reviewed(blocker())])
    task = await harness.start_dev()
    assert await harness.kinds(task) == [TRIAGE, DEV, REVIEW, DEV, REVIEW]
    assert task.column is Column.HUMAN_REVIEW
    assert NEEDS_DECISION in task.labels
    review = await harness.tasks.latest_review(MAINTAINER.user_id, task.id)
    assert review is not None
    assert (review.round, review.verdict) == (2, ReviewVerdict.NEEDS_DECISION)
    second = harness.runner.seen[-1]
    assert second.review is not None
    assert second.review.round == 1


async def test_an_uncited_blocker_is_a_nit_and_the_card_is_approved() -> None:
    harness = Harness()
    harness.runner.reviews.append(reviewed(blocker(rubric_id=None)))
    task = await harness.start_dev()
    assert task.column is Column.HUMAN_REVIEW
    review = await harness.tasks.latest_review(MAINTAINER.user_id, task.id)
    assert review is not None
    assert review.findings[0].severity is ReviewSeverity.NIT
    assert review.findings[0].downgraded


async def test_a_finding_outside_the_branch_is_dropped() -> None:
    harness = Harness()
    harness.runner.reviews.append(reviewed(blocker(path="backend/src/app/main.py")))
    task = await harness.start_dev()
    review = await harness.tasks.latest_review(MAINTAINER.user_id, task.id)
    assert review is not None
    assert review.findings == ()
    assert task.column is Column.HUMAN_REVIEW


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (AgentResult(is_success=True, outcome="Done.", cost_usd=0.4), NO_REVIEW),
        (AgentResult(is_success=False, outcome="Budget spent.", cost_usd=0.4), "Budget spent."),
    ],
)
async def test_a_review_run_without_a_review_blocks_the_card(
    result: AgentResult, reason: str
) -> None:
    harness = Harness()
    harness.runner.reviews.append(result)
    task = await harness.start_dev()
    assert task.column is Column.BLOCKED
    assert task.blocked_reason == reason
    run = await harness.latest_run(task, REVIEW)
    assert run.status is RunStatus.FAILED
    assert run.cost_usd == 0.4
    assert await harness.tasks.latest_review(MAINTAINER.user_id, task.id) is None


async def test_a_human_request_for_changes_becomes_the_rework_blocker() -> None:
    harness = Harness()
    harness.runner.reviews.extend([reviewed(blocker()), reviewed(blocker())])
    task = await _human_review(harness)
    assert NEEDS_DECISION in task.labels
    done = await _request_changes(harness, task, "  Keep the old route name.  ")
    rework = next(c for c in reversed(harness.runner.seen) if c.run.kind is DEV)
    assert rework.run.round == 2
    assert rework.review is not None
    assert rework.review.round == 2
    assert rework.review.verdict is ReviewVerdict.CHANGES_REQUESTED
    assert rework.review.run_id == rework.run.id
    (note,) = rework.review.findings
    assert (note.severity, note.path) == (ReviewSeverity.BLOCKER, "")
    assert note.evidence == note.repro == "Keep the old route name."
    # The rework published again and the (clean) third review sent it back to human review.
    assert done.column is Column.HUMAN_REVIEW
    assert done.review_round == 3
    assert NEEDS_DECISION not in done.labels


async def test_requesting_changes_needs_notes_and_a_maintainer() -> None:
    harness = Harness()
    task = await _human_review(harness)
    with pytest.raises(ValidationFailed):
        await _request_changes(harness, task, "   ")
    with pytest.raises(Forbidden):
        await _request_changes(harness, task, "Rename it.", actor=USER)


async def test_a_maintainer_marks_a_reviewed_card_done() -> None:
    harness = Harness()
    task = await _human_review(harness)
    with pytest.raises(Forbidden):
        await harness.board.move(USER, task.id, MoveRequest(Column.DONE, "U", task.version))
    to_done = MoveRequest(Column.DONE, "U", task.version)
    done = await harness.board.move(MAINTAINER, task.id, to_done)
    await harness.dispatcher.drain()
    assert done.column is Column.DONE
    assert await harness.kinds(done) == [TRIAGE, DEV, REVIEW]


async def test_human_notes_are_stored_without_pii() -> None:
    harness = Harness()
    task = await _human_review(harness)
    await _request_changes(harness, task, "Ask user@example.test or call +61 400 000 000.")
    rework = next(c for c in reversed(harness.runner.seen) if c.run.kind is DEV)
    assert rework.review is not None
    (note,) = rework.review.findings
    stored = f"{note.evidence} {note.repro}"
    assert "user@example.test" not in stored
    assert "400 000 000" not in stored
    assert note.evidence.startswith("Ask [redacted]")


async def test_a_second_human_request_reaches_round_four_and_still_serialises() -> None:
    harness = Harness()
    harness.runner.reviews.extend([reviewed(blocker())] * 3)
    task = await _human_review(harness)  # round 2: needs a decision
    task = await _request_changes(harness, task, "First notes.")  # round 3: blockers again
    assert task.review_round == 3
    assert NEEDS_DECISION in task.labels
    task = await _request_changes(harness, task, "Second notes.")  # round 4: clean
    assert task.column is Column.HUMAN_REVIEW
    assert task.review_round == 4
    review = await harness.tasks.latest_review(MAINTAINER.user_id, task.id)
    assert review is not None
    assert (review.round, review.verdict) == (4, ReviewVerdict.APPROVED)
    assert mapping.review(review).round == 4
    assert mapping.task(task, MAINTAINER).review_round == 4
    # Runs carry the real round too (`runs.round` allows 0-20).
    rounds = {
        mapping.run(run).round
        for run in await harness.runs.for_task(MAINTAINER.user_id, task.id, 0, 50)
    }
    assert 4 in rounds
