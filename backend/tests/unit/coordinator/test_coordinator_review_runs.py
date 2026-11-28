"""Review runs (the sandboxed reviewer) and rework dev runs through the coordinator.

A review run's `get_task` is isolated: the card text, the plan's acceptance criteria, the base
SHA, the branch, the diff stat, the dev run's test summary (the sandbox's own re-run, read from
the dev run's outcome) and, from round 2, the previous blockers. Never the writer's steps,
handoff summary, subtasks or leases. Review runs are read-only (no claims).

A rework dev run (the card has a branch and the latest review asked for changes) also gets
`review_blockers`: the BLOCKER findings it must fix.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.coordinator.server import build_app
from app.coordinator.service import Scope
from app.coordinator.tokens import verify
from app.domain.devboard.model import (
    NewStep,
    ReviewFinding,
    ReviewRecord,
    ReviewSeverity,
    ReviewVerdict,
    RunKind,
    RunRecord,
    RunStatus,
    StepKind,
    TaskRecord,
)
from app.domain.devboard.publisher import DevRunSummary, dev_run_outcome
from app.domain.errors import Forbidden
from tests.unit.coordinator.support import BASE_URL, OWNER, Harness, call, harness

BRANCH = "agent/aaaaaaaa-bbbbbbbb"
BASE = "d" * 40
WRITER_SUMMARY = "WRITER-HANDOFF-SUMMARY-0001"
WRITER_STEP = "WRITER-STEP-THOUGHT-0001"
FEATURE = "web/src/features/contacts/ContactList.tsx"
REVIEWED_AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
REVIEW_ONLY_KEYS = {
    "task",
    "run",
    "budget",
    "review",
    "brief",
    "acceptance_criteria",
    "previous_blockers",
    "brief_complete",
    "next_cursor",
    "total_items",
}


def _now(h: Harness) -> int:
    return int(h.clock.now().timestamp())


async def _card(h: Harness, *, branch: str | None = BRANCH, review_round: int = 1) -> TaskRecord:
    task = await h.add_task()
    published = replace(task, branch=branch, base_sha=BASE if branch else None)
    return await h.tasks.save(OWNER, replace(published, review_round=review_round), task.version)


async def _finished_dev_run(h: Harness, task: TaskRecord, outcome: str) -> RunRecord:
    run = await h.add_run(task, agent="writer", status=RunStatus.RUNNING)
    await h.runs.append_step(
        OWNER, run.id, NewStep(kind=StepKind.THOUGHT, summary=WRITER_STEP), h.clock.now()
    )
    handoff = json.dumps({"status": "done", "summary": WRITER_SUMMARY})
    await h.runs.append_step(
        OWNER, run.id, NewStep(kind=StepKind.HANDOFF, summary=handoff), h.clock.now()
    )
    done = replace(run, status=RunStatus.SUCCEEDED, outcome=outcome)
    h.runs.runs[run.id] = done
    return done


def _outcome(branch: str = BRANCH, *, failed: int = 1) -> str:
    summary = DevRunSummary(
        branch=branch,
        files_changed=3,
        insertions=40,
        deletions=5,
        passed=12,
        failed=failed,
        first_failing="web/src/features/contacts/empty.test.tsx::shows" if failed else None,
    )
    return dev_run_outcome(summary, "done")


async def _scope(h: Harness, task: TaskRecord, kind: RunKind, agent: str) -> Scope:
    run = await h.add_run(task, agent=agent, kind=kind)
    return await h.service.authorize(verify(h.runtime.key, h.token(task.id, run.id), now=_now(h)))


def _review(task: TaskRecord, verdict: ReviewVerdict, *findings: ReviewFinding) -> ReviewRecord:
    return ReviewRecord(
        id=uuid.uuid4(),
        task_id=task.id,
        run_id=uuid.uuid4(),
        round=1,
        verdict=verdict,
        summary="Round one.",
        findings=findings,
        created_at=REVIEWED_AT,
    )


BLOCKER = ReviewFinding(
    ReviewSeverity.BLOCKER,
    FEATURE,
    "The empty state is never rendered.",
    rubric_id="TEST-01",
    line=14,
    fix="Render EmptyState when the list is empty.",
)
CITED_BY_TEST = ReviewFinding(
    ReviewSeverity.BLOCKER,
    FEATURE,
    "The test fails.",
    test_id="web/src/features/contacts/empty.test.tsx::shows",
)
NIT = ReviewFinding(ReviewSeverity.NIT, FEATURE, "Name could be clearer.")


# ------------------------------------------------------------------ review runs
async def test_a_review_run_gets_only_its_isolated_context() -> None:
    h = harness()
    task = await _card(h)
    await _finished_dev_run(h, task, _outcome())
    scope = await _scope(h, task, RunKind.REVIEW, "reviewer")
    page = await h.service.get_task(scope, 0)
    assert set(page) == REVIEW_ONLY_KEYS
    assert page["brief"] == "Add an empty state to the contacts list."
    assert page["acceptance_criteria"] == [
        {"test_id": "web/src/features/contacts/empty.test.tsx::shows", "expectation": "Shows it."}
    ]
    assert page["review"] == {
        "base_sha": BASE,
        "branch": BRANCH,
        "round": 1,
        "diff_stat": {"files_changed": 3, "insertions": 40, "deletions": 5},
        "dev_tests": {
            "passed": 12,
            "failed": 1,
            "first_failing": "web/src/features/contacts/empty.test.tsx::shows",
        },
    }
    assert page["previous_blockers"] == []
    text = json.dumps(page)
    for hidden in (WRITER_SUMMARY, WRITER_STEP, "frontend-engineer", "lease", "Small, self"):
        assert hidden not in text


async def test_the_review_reads_the_dev_run_that_published_the_card_branch() -> None:
    h = harness()
    task = await _card(h)
    await _finished_dev_run(h, task, _outcome(failed=0))
    h.clock.advance(timedelta(seconds=60))
    await _finished_dev_run(h, task, _outcome("agent/aaaaaaaa-cccccccc", failed=7))
    scope = await _scope(h, task, RunKind.REVIEW, "reviewer")
    review = (await h.service.get_task(scope, 0))["review"]
    assert isinstance(review, dict)
    assert review["dev_tests"] == {"passed": 12, "failed": 0, "first_failing": None}


async def test_without_a_matching_dev_run_the_counts_are_unknown() -> None:
    h = harness()
    task = await _card(h)
    await _finished_dev_run(h, task, "Stopped.")
    scope = await _scope(h, task, RunKind.REVIEW, "reviewer")
    review = (await h.service.get_task(scope, 0))["review"]
    assert isinstance(review, dict)
    assert review["diff_stat"] is None
    assert review["dev_tests"] is None


async def test_a_round_two_review_sees_the_previous_blockers_only() -> None:
    h = harness()
    task = await _card(h, review_round=2)
    await h.tasks.add_review(
        OWNER, _review(task, ReviewVerdict.CHANGES_REQUESTED, BLOCKER, NIT, CITED_BY_TEST)
    )
    scope = await _scope(h, task, RunKind.REVIEW, "reviewer")
    page = await h.service.get_task(scope, 0)
    assert page["previous_blockers"] == [
        {
            "path": FEATURE,
            "line": 14,
            "citation": "TEST-01",
            "evidence": "The empty state is never rendered.",
            "fix": "Render EmptyState when the list is empty.",
        },
        {
            "path": FEATURE,
            "line": None,
            "citation": "web/src/features/contacts/empty.test.tsx::shows",
            "evidence": "The test fails.",
            "fix": None,
        },
    ]
    assert "review_blockers" not in page


async def test_a_review_run_can_not_claim_leases() -> None:
    h = harness()
    scope = await _scope(h, await _card(h), RunKind.REVIEW, "reviewer")
    with pytest.raises(Forbidden):
        await h.service.claim_paths(scope, ("web/src/features/",), 0)
    assert h.leases.leases == {}


async def test_review_run_over_http_reads_its_context_but_claim_is_403() -> None:
    h = harness()
    scope = await _scope(h, await _card(h), RunKind.REVIEW, "reviewer")
    token = h.token(scope.task.id, scope.run.id)
    app = build_app(h.runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http,
    ):
        task = await call(http, token, "GET", "/v1/task")
        assert task.status_code == 200, task.text
        assert task.json()["review"]["branch"] == BRANCH
        claim = await call(http, token, "POST", "/v1/leases/claim", {"paths": ["web/src/"]})
        assert claim.status_code == 403, claim.text
    assert h.leases.leases == {}


# ------------------------------------------------------------------ rework dev runs
async def test_a_rework_dev_run_gets_the_review_blockers() -> None:
    h = harness()
    task = await _card(h)
    await h.tasks.add_review(OWNER, _review(task, ReviewVerdict.CHANGES_REQUESTED, BLOCKER, NIT))
    scope = await _scope(h, task, RunKind.DEV, "writer")
    page = await h.service.get_task(scope, 0)
    assert page["review_blockers"] == [
        {
            "path": FEATURE,
            "line": 14,
            "citation": "TEST-01",
            "evidence": "The empty state is never rendered.",
            "fix": "Render EmptyState when the list is empty.",
        }
    ]
    # A writer still gets its plan and leases.
    assert page["subtasks"]
    assert page["lease_paths"]


async def test_a_first_dev_run_has_no_review_blockers() -> None:
    h = harness()
    task = await _card(h, branch=None)
    await h.tasks.add_review(OWNER, _review(task, ReviewVerdict.CHANGES_REQUESTED, BLOCKER))
    page = await h.service.get_task(await _scope(h, task, RunKind.DEV, "writer"), 0)
    assert page["review_blockers"] == []


async def test_an_approved_review_gives_no_blockers() -> None:
    h = harness()
    task = await _card(h)
    await h.tasks.add_review(OWNER, _review(task, ReviewVerdict.APPROVED, NIT))
    page = await h.service.get_task(await _scope(h, task, RunKind.DEV, "writer"), 0)
    assert page["review_blockers"] == []
