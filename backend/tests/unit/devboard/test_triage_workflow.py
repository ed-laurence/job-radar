"""The workflow applies triage's effective route (`now` starts the plan as a maintainer's drag
would, `next` tops Triaged, `later` goes last), fails runs that name paths outside the repo or
a duplicate that isn't an open card (the card stays put), shows triage the other open cards, and
checks the plan's criteria and leases against the repo too."""

from __future__ import annotations

from dataclasses import replace

from app.domain.devboard.model import (
    Column,
    Criterion,
    MoveRequest,
    Priority,
    Route,
    RunKind,
    RunStatus,
    Subtask,
    Verification,
    VerificationStatus,
)
from app.domain.devboard.workflow import MAX_OPEN_CARDS
from tests.unit.devboard.workflow_harness import MAINTAINER, USER, Harness, decision, draft


async def test_next_goes_to_the_top_of_triaged_and_stores_the_ratings() -> None:
    harness = Harness()
    first = await harness.create("First")
    harness.triage.answers["Second"] = decision(priority=Priority.P1)
    second = await harness.create("Second")
    assert (first.column, second.column) == (Column.TRIAGED, Column.TRIAGED)
    assert second.rank < first.rank
    assert (second.priority, second.route) == (Priority.P1, Route.NEXT)
    run = await harness.latest_run(second, RunKind.TRIAGE)
    assert run.status is RunStatus.SUCCEEDED
    assert run.outcome == "In scope: P1, minor, route next."


async def test_later_goes_to_the_bottom_of_triaged() -> None:
    harness = Harness()
    first = await harness.create("First")
    harness.triage.answers["Last"] = decision(route=Route.LATER)
    last = await harness.create("Last")
    assert last.column is Column.TRIAGED
    assert last.rank > first.rank
    assert last.route is Route.LATER


async def test_now_moves_on_to_plan_review_and_starts_the_plan() -> None:
    harness = Harness()
    harness.triage.answers["Urgent"] = decision(route=Route.NOW, priority=Priority.P0)
    task = await harness.create("Urgent")
    assert task.column is Column.PLAN_REVIEW
    assert task.route is Route.NOW
    assert harness.runner.runs == [RunKind.PLAN]
    plan_run = await harness.latest_run(task, RunKind.PLAN)
    assert plan_run.status is RunStatus.SUCCEEDED
    plan = await harness.plans.get(MAINTAINER, task.id)
    assert plan.acceptance_criteria == draft().acceptance_criteria
    events = await harness.tasks.events(MAINTAINER.user_id, task.id, 0, 20)
    moves = [(e.from_column, e.to_column) for e in reversed(events) if e.to_column is not None]
    assert moves[-2:] == [
        (Column.BACKLOG, Column.TRIAGED),
        (Column.TRIAGED, Column.PLAN_REVIEW),
    ]


async def test_now_from_the_testers_card_plans_like_a_maintainers() -> None:
    # The maintainer and the tester are treated the same: triage's `now` starts planning.
    harness = Harness()
    harness.triage.answers["Urgent"] = decision(route=Route.NOW)
    task = await harness.create("Urgent", actor=USER)
    assert task.column is Column.PLAN_REVIEW
    assert task.route is Route.NOW
    assert harness.runner.runs == [RunKind.PLAN]


async def test_unclear_now_is_capped_at_next() -> None:
    harness = Harness()
    unclear = Verification(VerificationStatus.UNCLEAR, "Can't tell from the card.")
    harness.triage.answers["Vague"] = decision(route=Route.NOW, verification=unclear)
    task = await harness.create("Vague")
    assert (task.column, task.route) == (Column.TRIAGED, Route.NEXT)
    assert harness.runner.runs == []


async def test_invented_paths_fail_the_run_and_the_card_stays_in_backlog() -> None:
    harness = Harness()
    harness.triage.answers["Bad"] = decision(
        likely_paths=("web/src/components/devboard/Metrics.tsx",)
    )
    task = await harness.create("Bad")
    assert task.column is Column.BACKLOG
    assert task.priority is None
    run = await harness.latest_run(task, RunKind.TRIAGE)
    assert run.status is RunStatus.FAILED
    assert run.outcome == (
        "Triage named paths that are not in the repo: web/src/components/devboard/Metrics.tsx"
    )
    assert await harness.tasks.latest_triage(USER.user_id, task.id) is None


async def test_out_of_scope_cards_skip_the_path_check() -> None:
    harness = Harness()
    harness.triage.answers["Infra"] = decision(likely_paths=("infra/modules/backup/",))
    task = await harness.create("Infra")
    assert task.column is Column.OUT_OF_SCOPE


async def test_a_duplicate_of_an_open_card_goes_last() -> None:
    harness = Harness()
    original = await harness.create("Original")
    duplicate = Verification(VerificationStatus.DUPLICATE, "Same as the first.", (), original.id)
    harness.triage.answers["Copy"] = decision(route=Route.NOW, verification=duplicate)
    copy = await harness.create("Copy")
    assert (copy.column, copy.route) == (Column.TRIAGED, Route.LATER)
    assert copy.rank > original.rank
    stored = await harness.tasks.latest_triage(USER.user_id, copy.id)
    assert stored is not None
    assert stored.verification.duplicate_of == original.id


async def test_a_duplicate_of_an_unknown_card_fails_the_run() -> None:
    harness = Harness()
    unknown = Verification(VerificationStatus.DUPLICATE, "Same as another.", (), USER.user_id)
    harness.triage.answers["Copy"] = decision(verification=unknown)
    task = await harness.create("Copy")
    assert task.column is Column.BACKLOG
    run = await harness.latest_run(task, RunKind.TRIAGE)
    assert run.status is RunStatus.FAILED
    assert "not an open card" in str(run.outcome)


async def test_triage_sees_the_other_open_cards_but_not_itself_or_done_cards() -> None:
    harness = Harness()
    first = await harness.create("First")
    done = await harness.create("Finished")
    await harness.tasks.save(USER.user_id, replace(done, column=Column.DONE), done.version)
    await harness.create("Third")
    seen = harness.triage.seen[-1]
    assert [(card.id, card.title, card.column) for card in seen.open_cards] == [
        (first.id, "First", Column.TRIAGED)
    ]


async def test_open_cards_are_capped() -> None:
    harness = Harness()
    for index in range(MAX_OPEN_CARDS + 2):
        await harness.board.create(MAINTAINER, f"Card {index}", "", ())
    await harness.dispatcher.drain()
    assert len(harness.triage.seen[-1].open_cards) == MAX_OPEN_CARDS


async def test_a_plan_naming_paths_outside_the_repo_fails_and_keeps_no_plan() -> None:
    harness = Harness()
    harness.runner.plan = draft(
        subtasks=(
            Subtask("t0", "Tests", "qa-expert", ("web/tests/e2e/",), is_test_only=True),
            Subtask("s1", "Fix", "frontend-engineer", ("web/src/widgets/",), ("t0",)),
        ),
        acceptance_criteria=(Criterion("backend/tests/unit/nowhere/test_x.py::y", "holds"),),
    )
    task = await harness.create("Plan me")
    await harness.board.move(
        MAINTAINER, task.id, MoveRequest(Column.PLAN_REVIEW, "U", task.version)
    )
    await harness.dispatcher.drain()
    run = await harness.latest_run(task, RunKind.PLAN)
    assert run.status is RunStatus.FAILED
    assert run.outcome == (
        "The plan named paths that are not in the repo: "
        "backend/tests/unit/nowhere/test_x.py, web/src/widgets/"
    )
    assert run.cost_usd == 0.5  # the refused run keeps its spend
    assert await harness.plans.latest(MAINTAINER, task.id) is None


async def test_regenerated_leases_need_not_be_in_the_listing() -> None:
    harness = Harness()
    harness.runner.plan = draft(
        subtasks=(
            Subtask("t0", "Tests", "qa-expert", ("web/tests/e2e/",), is_test_only=True),
            Subtask("c1", "Contract", "backend-engineer", ("api/openapi.yaml",), ("t0",)),
        ),
    )
    task = await harness.create("Contract")
    await harness.board.move(
        MAINTAINER, task.id, MoveRequest(Column.PLAN_REVIEW, "U", task.version)
    )
    await harness.dispatcher.drain()
    plan = await harness.plans.get(MAINTAINER, task.id)
    assert "backend/src/app/generated/" in plan.subtasks[1].lease_paths
