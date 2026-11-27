"""Coordinator tools against the in-memory board: scoping, caps, refusals and lease waits."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.coordinator.schemas import RESULT_TOKENS, MessageTooLarge
from app.coordinator.service import Scope
from app.coordinator.tokens import verify
from app.domain.devboard.messages import estimate_tokens
from app.domain.devboard.model import Column, RunStatus, StepKind
from app.domain.errors import Forbidden, IllegalTransition, ValidationFailed
from tests.unit.coordinator.support import OTHER_OWNER, OWNER, Harness, harness


def _now(h: Harness) -> int:
    return int(h.clock.now().timestamp())


async def _scope(
    h: Harness, *, body: str = "Add an empty state.", agent: str = "frontend-engineer"
) -> Scope:
    task = await h.add_task(body=body)
    run = await h.add_run(task, agent=agent)
    grant = verify(h.runtime.key, h.token(task.id, run.id), now=_now(h))
    return await h.service.authorize(grant)


def _handoff(**overrides: object) -> dict[str, object]:
    handoff: dict[str, object] = {
        "status": "done",
        "summary": "Added the contacts empty state with tests.",
        "changed_paths": ["web/src/features/contacts/Empty.tsx"],
        "tests": {"passed": 3, "failed": 0},
        "rubric_checked": ["ARCH-40"],
        "open_questions": [],
        "skipped_checks": ["none"],
    }
    handoff.update(overrides)
    return handoff


# ------------------------------------------------------------------ authorisation
async def test_authorize_scopes_to_the_task_owner_as_an_agent() -> None:
    h = harness()
    scope = await _scope(h)
    assert scope.actor.user_id == OWNER
    assert scope.actor.kind.value == "agent"
    assert scope.run.task_id == scope.task.id


async def test_a_run_of_another_task_is_refused() -> None:
    h = harness()
    mine = await h.add_task()
    theirs = await h.add_task(owner=OTHER_OWNER)
    their_run = await h.add_run(theirs)
    grant = verify(h.runtime.key, h.token(mine.id, their_run.id), now=_now(h))
    with pytest.raises(Forbidden):
        await h.service.authorize(grant)


@pytest.mark.parametrize("status", [RunStatus.SUCCEEDED, RunStatus.KILLED, RunStatus.FAILED])
async def test_a_finished_run_is_refused(status: RunStatus) -> None:
    h = harness()
    task = await h.add_task()
    run = await h.add_run(task, status=status)
    with pytest.raises(Forbidden):
        await h.service.authorize(verify(h.runtime.key, h.token(task.id, run.id), now=_now(h)))


async def test_a_child_writer_run_is_an_active_run_of_its_task() -> None:
    h = harness()
    task = await h.add_task()
    lead = await h.add_run(task, agent="dev-lead")
    writer = await h.add_run(task, agent="backend-engineer", parent=lead.id)
    grant = verify(h.runtime.key, h.token(task.id, writer.id), now=_now(h))
    assert (await h.service.authorize(grant)).run.id == writer.id


async def test_a_deleted_task_is_refused() -> None:
    h = harness()
    task = await h.add_task()
    run = await h.add_run(task)
    h.tasks.deleted[task.id] = h.clock.now()
    with pytest.raises(Forbidden):
        await h.service.authorize(verify(h.runtime.key, h.token(task.id, run.id), now=_now(h)))


# ------------------------------------------------------------------ get_task
async def test_get_task_returns_brief_criteria_plan_leases_and_budget() -> None:
    h = harness()
    scope = await _scope(h)
    page = await h.service.get_task(scope, 0)
    assert page["brief"] == "Add an empty state."
    assert page["brief_complete"] is True
    assert page["next_cursor"] is None
    assert page["acceptance_criteria"] == [
        {"test_id": "web/src/features/contacts/empty.test.tsx::shows", "expectation": "Shows it."}
    ]
    assert page["acceptance_tests"] == ["web/src/features/contacts/empty.test.tsx::shows"]
    assert page["lease_paths"] == ["web/src/features/"]  # the run's own subtask
    assert page["budget"] == {"usd": 1.0, "minutes": 30}
    plan = page["plan"]
    assert isinstance(plan, dict)
    assert plan["approved"] is True
    subtasks = page["subtasks"]
    assert isinstance(subtasks, list)
    assert len(subtasks) == 2


async def test_the_lead_sees_every_subtask_lease_path() -> None:
    h = harness()
    scope = await _scope(h, agent="dev-lead")
    page = await h.service.get_task(scope, 0)
    assert page["lease_paths"] == ["web/src/features/", "backend/src/app/api/"]


async def test_a_long_brief_is_paged_under_the_result_cap_and_nothing_is_lost() -> None:
    h = harness()
    body = " ".join(f"word{index}" for index in range(1400))  # ~8000 chars, the body maximum
    scope = await _scope(h, body=body)
    cursor: int | None = 0
    brief, pages = "", 0
    while cursor is not None:
        page = await h.service.get_task(scope, cursor)
        assert estimate_tokens(json.dumps(page)) <= RESULT_TOKENS
        assert isinstance(page["brief"], str)
        brief += page["brief"]
        next_cursor = page["next_cursor"]
        assert next_cursor is None or isinstance(next_cursor, int)
        cursor = next_cursor
        pages += 1
    assert brief == body
    assert pages > 1


async def test_get_task_refuses_a_cursor_past_the_end() -> None:
    h = harness()
    scope = await _scope(h)
    with pytest.raises(ValidationFailed):
        await h.service.get_task(scope, 999)


# ------------------------------------------------------------------ leases
async def test_claim_paths_grants_and_release_frees_only_this_run() -> None:
    h = harness()
    scope = await _scope(h)
    other = await _scope(h)
    granted = await h.service.claim_paths(scope, ("web/src/features/contacts/",), 0)
    assert granted["waiting"] == []
    assert granted["granted"] == [
        {
            "path": "web/src/features/contacts/",
            "kind": "exclusive",
            "expires_at": (h.clock.now() + timedelta(minutes=10)).isoformat(),
        }
    ]
    await h.service.claim_paths(other, ("web/src/features/board/",), 0)
    assert await h.service.release_paths(scope) == {"released": 1}
    live = [lease.run_id for lease in h.leases.leases.values() if lease.released_at is None]
    assert live == [other.run.id]


async def test_an_overlapping_claim_times_out_without_naming_the_other_task() -> None:
    h = harness()
    holder = await _scope(h)
    waiter = await _scope(h)
    await h.service.claim_paths(holder, ("web/src/features/",), 0)
    refused = await h.service.claim_paths(waiter, ("web/src/features/contacts/x.tsx",), 3)
    assert refused["granted"] == []
    assert refused["waiting"] == ["web/src/features/"]
    assert refused["waited_s"] == 3
    assert str(holder.task.id) not in json.dumps(refused)
    assert str(holder.run.id) not in json.dumps(refused)
    assert sum(h.slept) == 3


async def test_claim_paths_blocks_until_the_other_lease_is_released() -> None:
    h = harness()
    holder = await _scope(h)
    waiter = await _scope(h)
    await h.service.claim_paths(holder, ("web/src/features/",), 0)

    async def release_after_two_seconds() -> None:
        if sum(h.slept) >= 2:
            await h.lease_service.release_run(holder.actor, holder.run.id)

    h.on_sleep.append(release_after_two_seconds)
    granted = await h.service.claim_paths(waiter, ("web/src/features/contacts/",), 60)
    assert granted["waiting"] == []
    assert granted["waited_s"] == 2
    assert len(h.leases.waits) == 1  # queued once, not on every poll


async def test_claim_paths_with_zero_wait_does_not_sleep() -> None:
    h = harness()
    holder = await _scope(h)
    waiter = await _scope(h)
    await h.service.claim_paths(holder, ("web/src/lib/",), 0)
    refused = await h.service.claim_paths(waiter, ("web/src/lib/api.ts",), 0)
    assert refused["waiting"] == ["web/src/lib/"]
    assert h.slept == []


async def test_a_run_killed_while_waiting_loses_access() -> None:
    h = harness()
    holder = await _scope(h)
    waiter = await _scope(h)
    await h.service.claim_paths(holder, ("web/src/features/",), 0)

    async def kill() -> None:
        h.runs.runs[waiter.run.id] = replace(h.runs.runs[waiter.run.id], status=RunStatus.KILLED)

    h.on_sleep.append(kill)
    with pytest.raises(Forbidden):
        await h.service.claim_paths(waiter, ("web/src/features/a.tsx",), 30)


@pytest.mark.parametrize("path", ["../etc/passwd", "web/src/**/*.ts", "/abs/path", "infra/main.tf"])
async def test_bad_or_out_of_scope_paths_are_refused_before_anything_is_queued(path: str) -> None:
    h = harness()
    scope = await _scope(h)
    with pytest.raises(ValidationFailed):
        await h.service.claim_paths(scope, (path,), 5)
    assert h.leases.waits == []
    assert h.slept == []


async def test_release_with_paths_must_cover_every_held_lease() -> None:
    h = harness()
    scope = await _scope(h)
    await h.service.claim_paths(scope, ("web/src/features/a/", "web/src/features/b/"), 0)
    with pytest.raises(ValidationFailed):
        await h.service.release_paths(scope, ("web/src/features/a/",))
    assert all(lease.released_at is None for lease in h.leases.leases.values())
    both = ("web/src/features/a/", "web/src/features/b/")
    assert await h.service.release_paths(scope, both) == {"released": 2}


async def test_oversized_messages_are_refused_as_too_large_not_invalid() -> None:
    h = harness()
    scope = await _scope(h)
    with pytest.raises(MessageTooLarge):
        await h.service.post_handoff(scope, json.dumps(_handoff(summary=" ".join(["w"] * 81))))
    with pytest.raises(MessageTooLarge):
        await h.service.request_help(scope, " ".join(["help"] * 160))
    with pytest.raises(ValidationFailed) as invalid:
        await h.service.post_handoff(scope, json.dumps(_handoff(status="finished")))
    assert not isinstance(invalid.value, MessageTooLarge)


async def test_renew_lease_heartbeats_only_this_runs_leases() -> None:
    h = harness()
    scope = await _scope(h)
    other = await _scope(h)
    await h.service.claim_paths(scope, ("web/src/features/a/",), 0)
    await h.service.claim_paths(other, ("web/src/features/b/",), 0)
    h.clock.advance(timedelta(minutes=5))
    renewed = await h.service.renew_lease(scope)
    assert renewed["renewed"] == 1
    others = [lease for lease in h.leases.leases.values() if lease.run_id == other.run.id]
    assert all(lease.heartbeat_at is None for lease in others)


# ------------------------------------------------------------------ steps and handoffs
async def test_post_step_is_redacted_and_stored_on_this_run() -> None:
    h = harness()
    scope = await _scope(h)
    text = "Wrote tests; key AKIAABCDEFGHIJKLMNOP and proj-xxxx-internal and a@example.test"
    result = await h.service.post_step(scope, StepKind.MESSAGE, text)
    assert result == {"seq": 1}
    (step,) = h.runs.steps[scope.run.id]
    assert "AKIA" not in step.summary
    assert "proj-xxxx-internal" not in step.summary
    assert "a@example.test" not in step.summary
    assert step.agent == "frontend-engineer"


async def test_post_handoff_accepts_the_agents_md_shape() -> None:
    h = harness()
    scope = await _scope(h)
    result = await h.service.post_handoff(scope, json.dumps(_handoff()))
    assert result["accepted"] is True
    (step,) = h.runs.steps[scope.run.id]
    assert step.kind is StepKind.HANDOFF
    assert json.loads(step.summary)["skipped_checks"] == ["none"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": " ".join(["word"] * 81)},
        {"open_questions": ["a?", "b?", "c?", "d?"]},
        {"changed_paths": [f"web/src/features/x{index}/file.tsx" for index in range(60)]},
        {"summary": "Done.\n```python\nprint(1)\n```"},
        {"status": "finished"},
        {"extra": "field"},
        {"tests": {"passed": 1}},
    ],
)
async def test_post_handoff_refuses_oversized_or_malformed_handoffs(
    overrides: dict[str, object],
) -> None:
    h = harness()
    scope = await _scope(h)
    with pytest.raises(ValidationFailed):
        await h.service.post_handoff(scope, json.dumps(_handoff(**overrides)))
    assert h.runs.steps[scope.run.id] == []


async def test_request_help_blocks_the_task_with_a_redacted_reason() -> None:
    h = harness()
    scope = await _scope(h)
    result = await h.service.request_help(scope, "Lease wait on web/src/lib/; mail a@example.test")
    assert result["column"] == "blocked"
    task = await h.tasks.get(OWNER, scope.task.id)
    assert task is not None
    assert task.column is Column.BLOCKED
    assert task.blocked_from is Column.AGENT_DEV
    assert task.blocked_reason is not None
    assert "a@example.test" not in task.blocked_reason
    events = await h.tasks.events(OWNER, scope.task.id, 0, 10)
    assert events[0].run_id == scope.run.id
    assert events[0].actor_kind.value == "agent"
    again = await h.service.request_help(scope, "Still blocked.")
    assert again["column"] == "blocked"


async def test_request_help_over_200_tokens_is_refused_and_changes_nothing() -> None:
    h = harness()
    scope = await _scope(h)
    with pytest.raises(ValidationFailed):
        await h.service.request_help(scope, " ".join(["help"] * 160))
    task = await h.tasks.get(OWNER, scope.task.id)
    assert task is not None
    assert task.column is Column.AGENT_DEV
    assert h.runs.steps[scope.run.id] == []


async def test_request_help_on_a_done_task_is_refused() -> None:
    h = harness()
    scope = await _scope(h)
    task = await h.tasks.get(OWNER, scope.task.id)
    assert task is not None
    await h.tasks.save(OWNER, replace(task, column=Column.DONE), 1)
    with pytest.raises(IllegalTransition):
        await h.service.request_help(scope, "Help.")
