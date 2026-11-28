"""approveTaskPlan with test changes, and a dev-lead draft that code refuses (the run fails with
a one-line reason; no plan is stored)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace

import pytest

from app.adapters.fakes.devboard import InMemoryTaskStore
from app.compose import devboard as devboard_compose
from app.domain.devboard.model import Risk, Subtask
from app.domain.ports.devboard import AgentResult, PlanDraft, RunContext, RunTools
from app.main import Container
from tests.unit.devboard import cards
from tests.unit.devboard.support import AS_MAINTAINER, AS_USER, Board, board

TO_CHANGE = ("backend/tests/unit/test_contacts_search.py",)


@pytest.fixture
async def ui(container: Container) -> AsyncGenerator[Board]:
    async with board(container) as value:
        yield value


async def _in_review_with_test_changes(ui: Board) -> dict[str, object]:
    task = await ui.create(cards.API_SEARCH)
    assert (await ui.move(task, "plan_review")).status_code == 200
    store = ui.runtime.stores.tasks
    assert isinstance(store, InMemoryTaskStore)
    store.plans[-1] = replace(store.plans[-1], tests_to_change=TO_CHANGE)
    return await ui.task(str(task["id"]))


def _approval(task: dict[str, object], **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "plan_version": 1,
        "task_version": task["version"],
        "approve_tests": True,
        "approve_plan": True,
    }
    return body | extra


async def test_plan_lists_its_test_changes(ui: Board) -> None:
    task = await _in_review_with_test_changes(ui)
    plan = (await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_USER)).json()
    assert plan["tests_to_change"] == list(TO_CHANGE)


async def test_test_changes_without_allowance_are_422(ui: Board) -> None:
    task = await _in_review_with_test_changes(ui)
    url = f"/api/tasks/{task['id']}/plan/approve"
    response = await ui.http.post(url, json=_approval(task), headers=AS_MAINTAINER)
    assert response.status_code == 422
    assert response.json()["type"] == "/problems/validation-failed"
    assert "allow_test_changes" in response.json()["detail"]
    refused = await ui.http.post(
        url, json=_approval(task, allow_test_changes=False), headers=AS_MAINTAINER
    )
    assert refused.status_code == 422
    assert (await ui.task(str(task["id"])))["column"] == "plan_review"


async def test_allowing_test_changes_adds_the_label_and_approves(ui: Board) -> None:
    task = await _in_review_with_test_changes(ui)
    url = f"/api/tasks/{task['id']}/plan/approve"
    body = _approval(task, allow_test_changes=True)
    assert (await ui.http.post(url, json=body, headers=AS_USER)).status_code == 403
    response = await ui.http.post(url, json=body, headers=AS_MAINTAINER)
    assert response.status_code == 200, response.text
    assert response.json()["column"] == "ready"
    assert f"modify-tests:{TO_CHANGE[0]}" in response.json()["labels"]


class _BadPlanner:
    async def run(self, context: RunContext, tools: RunTools) -> AgentResult:
        assert tools is not None
        assert context is not None
        draft = PlanDraft(
            mode="single",
            subtasks=(
                Subtask("t0", "Tests", "qa-expert", ("backend/tests/unit/",), is_test_only=True),
            ),
            acceptance_tests=("backend/tests/unit/conftest.py::fixture",),
            budget_usd=1.0,
            budget_minutes=10,
            llm_risk=Risk.LOW,
        )
        return AgentResult(is_success=True, outcome="Plan ready.", cost_usd=0.25, plan=draft)


def _bad_planner(*_args: object, **_kwargs: object) -> _BadPlanner:
    return _BadPlanner()


async def test_a_refused_draft_fails_the_plan_run_and_keeps_its_spend(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(devboard_compose, "agent_runner", _bad_planner)
    async with board(container) as ui:
        task = await ui.create(cards.API_SEARCH)
        assert (await ui.move(task, "plan_review")).status_code == 200
        plan = await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_USER)
        assert plan.status_code == 404
        runs = (await ui.http.get(f"/api/tasks/{task['id']}/runs", headers=AS_USER)).json()
        latest = runs["items"][0]
        assert (latest["kind"], latest["status"]) == ("plan", "failed")
        assert "acceptance test" in latest["outcome"]
        assert "\n" not in latest["outcome"]
        assert latest["cost_usd"] == pytest.approx(0.25)
