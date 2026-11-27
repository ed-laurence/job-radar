"""Plan-kind runs (the sandboxed dev-lead planner): read-only access to the coordinator.

A plan run may read its task (with the card's labels and the plan's tests_to_change), post
steps and ask for help, but it can never claim leases. Its token is still scoped to its task.
"""

from __future__ import annotations

import httpx
import pytest

from app.coordinator.server import build_app
from app.coordinator.service import Scope
from app.coordinator.tokens import verify
from app.domain.devboard.model import Column, RunKind, StepKind
from app.domain.errors import Forbidden
from tests.unit.coordinator.support import BASE_URL, OTHER_OWNER, Harness, call, harness

LABELS = ("modify-tests:backend/tests/unit/devboard/", "ui")
TESTS_TO_CHANGE = ("backend/tests/unit/devboard/test_triage.py", "web/src/a.test.tsx")


def _now(h: Harness) -> int:
    return int(h.clock.now().timestamp())


async def _plan_scope(h: Harness, *, with_plan: bool = True) -> Scope:
    task = await h.add_task(
        column=Column.TRIAGED,
        with_plan=with_plan,
        labels=LABELS,
        tests_to_change=TESTS_TO_CHANGE,
    )
    run = await h.add_run(task, agent="dev-lead", kind=RunKind.PLAN)
    grant = verify(h.runtime.key, h.token(task.id, run.id), now=_now(h))
    return await h.service.authorize(grant)


async def test_a_plan_run_is_authorised_for_its_own_task() -> None:
    h = harness()
    scope = await _plan_scope(h)
    assert scope.run.kind is RunKind.PLAN
    assert scope.actor.kind.value == "agent"


async def test_a_plan_run_token_for_another_task_is_refused() -> None:
    h = harness()
    mine = await h.add_task()
    theirs = await h.add_task(owner=OTHER_OWNER)
    their_plan_run = await h.add_run(theirs, agent="dev-lead", kind=RunKind.PLAN)
    grant = verify(h.runtime.key, h.token(mine.id, their_plan_run.id), now=_now(h))
    with pytest.raises(Forbidden):
        await h.service.authorize(grant)


async def test_get_task_for_a_plan_run_has_labels_and_tests_to_change() -> None:
    h = harness()
    scope = await _plan_scope(h)
    page = await h.service.get_task(scope, 0)
    task = page["task"]
    assert isinstance(task, dict)
    assert task["labels"] == list(LABELS)
    assert page["tests_to_change"] == list(TESTS_TO_CHANGE)
    assert page["next_cursor"] is None


async def test_get_task_before_any_plan_has_empty_tests_to_change() -> None:
    h = harness()
    scope = await _plan_scope(h, with_plan=False)
    page = await h.service.get_task(scope, 0)
    assert page["tests_to_change"] == []
    assert page["plan"] is None
    assert page["brief"] == "Add an empty state to the contacts list."


async def test_dev_runs_also_see_labels_and_tests_to_change() -> None:
    h = harness()
    task = await h.add_task(labels=LABELS, tests_to_change=TESTS_TO_CHANGE)
    run = await h.add_run(task)
    scope = await h.service.authorize(verify(h.runtime.key, h.token(task.id, run.id), now=_now(h)))
    page = await h.service.get_task(scope, 0)
    assert isinstance(page["task"], dict)
    assert page["task"]["labels"] == list(LABELS)
    assert page["tests_to_change"] == list(TESTS_TO_CHANGE)


async def test_a_plan_run_can_post_steps() -> None:
    h = harness()
    scope = await _plan_scope(h)
    assert await h.service.post_step(scope, StepKind.MESSAGE, "Read the board module.") == {
        "seq": 1
    }
    (step,) = h.runs.steps[scope.run.id]
    assert step.agent == "dev-lead"


async def test_a_plan_run_can_not_claim_leases_and_nothing_is_queued() -> None:
    h = harness()
    scope = await _plan_scope(h)
    with pytest.raises(Forbidden):
        await h.service.claim_paths(scope, ("web/src/features/",), 0)
    assert h.leases.leases == {}


async def test_plan_run_over_http_reads_and_steps_but_claim_is_403() -> None:
    h = harness()
    scope = await _plan_scope(h)
    token = h.token(scope.task.id, scope.run.id)
    app = build_app(h.runtime)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as http,
    ):
        task = await call(http, token, "GET", "/v1/task")
        assert task.status_code == 200, task.text
        assert task.json()["tests_to_change"] == list(TESTS_TO_CHANGE)
        step = await call(http, token, "POST", "/v1/steps", {"kind": "message", "text": "Hi."})
        assert step.status_code == 200, step.text
        claim = await call(http, token, "POST", "/v1/leases/claim", {"paths": ["web/src/"]})
        assert claim.status_code == 403, claim.text
        assert str(scope.task.id) not in claim.text
    assert h.leases.leases == {}
