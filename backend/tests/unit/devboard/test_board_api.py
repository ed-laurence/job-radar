"""Dev board over HTTP: CRUD, versions, the state machine, triage and the plan gate."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from app.main import Container
from tests.unit.devboard import cards
from tests.unit.devboard.support import AS_MAINTAINER, AS_USER, Board, board


@pytest.fixture
async def ui(container: Container) -> AsyncGenerator[Board]:
    async with board(container) as value:
        yield value


async def test_create_starts_in_backlog_and_triage_moves_it(ui: Board) -> None:
    response = await ui.http.post(
        "/api/tasks",
        json={"title": cards.API_SEARCH.title, "body": cards.API_SEARCH.body},
        headers=AS_USER,
    )
    assert response.status_code == 201
    created = response.json()
    assert created["column"] == "backlog"
    assert created["version"] == 1
    assert created["created_by_role"] == "user"
    assert "pr_url" not in created  # no nulls
    await ui.settle()
    task = await ui.task(created["id"])
    assert task["column"] == "triaged"
    assert (task["type"], task["size"], task["areas"]) == ("feature", "S", ["api"])
    triage = (await ui.http.get(f"/api/tasks/{created['id']}/triage", headers=AS_USER)).json()
    assert triage["in_scope"] is True
    # Triage verifies, rates and routes; it writes no acceptance criteria (the planner does).
    assert "acceptance_criteria" not in triage
    assert (triage["priority"], triage["severity"], triage["route"]) == ("P2", "minor", "next")
    assert triage["verification"]["status"] == "confirmed"
    assert triage["verification"]["code_refs"] == [
        "backend/src/app/api/jobs/crm.py",
        "backend/src/app/domain/jobs/crm.py",
    ]
    assert (task["priority"], task["severity"], task["route"]) == ("P2", "minor", "next")
    assert triage["provider"] == "replay"


async def test_risk_comes_from_paths_and_the_model_can_only_raise_it(ui: Board) -> None:
    api = await ui.create(cards.API_SEARCH)
    contract = await ui.create(cards.RUN_COST)
    assert api["risk"] == "medium"  # model said low; domain/api paths are medium
    assert contract["risk"] == "medium"  # model said low; api/openapi.yaml is medium


async def test_infra_work_is_out_of_scope_and_never_ready(ui: Board) -> None:
    task = await ui.create(cards.INFRA_BACKUP)
    assert task["column"] == "out_of_scope"
    assert "infrastructure" in str(task["out_of_scope_reason"])
    response = await ui.move(task, "ready")
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "/problems/illegal-transition"
    assert problem["status"] == 422
    assert "ready" not in problem["allowed_columns"]
    assert "backlog" in problem["allowed_columns"]


async def test_code_overrules_a_model_that_calls_test_infra_in_scope(ui: Board) -> None:
    task = await ui.create(cards.TEST_PLUGIN)
    assert task["column"] == "out_of_scope"
    assert "write scope" in str(task["out_of_scope_reason"])
    assert "backend/tests/conftest.py" in str(task["out_of_scope_reason"])


async def test_pii_is_redacted_before_save_and_the_author_is_told(ui: Board) -> None:
    response = await ui.http.post(
        "/api/tasks",
        json={"title": "Fix the invite", "body": "Mail alex@example.test or call +61 400 000 000"},
        headers=AS_USER,
    )
    task = response.json()
    assert task["pii_redacted"] is True
    assert "alex@example.test" not in task["body"]
    assert task["body"].count("[redacted]") == 2
    await ui.settle()
    events = (await ui.http.get(f"/api/tasks/{task['id']}/events", headers=AS_USER)).json()
    assert "pii_redacted" in [event["kind"] for event in events["items"]]


async def test_card_without_a_recording_fails_triage_and_stays_in_backlog(ui: Board) -> None:
    created = await ui.http.post("/api/tasks", json={"title": "Unrecorded"}, headers=AS_USER)
    await ui.settle()
    task_id = created.json()["id"]
    assert (await ui.task(task_id))["column"] == "backlog"
    runs = (await ui.http.get(f"/api/tasks/{task_id}/runs", headers=AS_USER)).json()["items"]
    assert runs[0]["status"] == "failed"
    assert "recording" in runs[0]["outcome"]


async def test_update_needs_the_current_version(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    url = f"/api/tasks/{task['id']}"
    stale = await ui.http.patch(url, json={"version": 1, "title": "x"}, headers=AS_USER)
    assert stale.status_code == 409
    assert stale.json()["current_version"] == task["version"]
    fresh = await ui.http.patch(
        url, json={"version": task["version"], "labels": ["ux"]}, headers=AS_USER
    )
    assert fresh.status_code == 200
    assert fresh.json()["version"] == int(str(task["version"])) + 1


async def test_only_maintainers_set_modify_tests_labels(ui: Board) -> None:
    response = await ui.http.post(
        "/api/tasks", json={"title": "t", "labels": ["modify-tests:web/tests/"]}, headers=AS_USER
    )
    assert response.status_code == 403


async def test_soft_delete(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    url = f"/api/tasks/{task['id']}"
    assert (await ui.http.delete(url, params={"version": 1}, headers=AS_USER)).status_code == 409
    ok = await ui.http.delete(url, params={"version": str(task["version"])}, headers=AS_USER)
    assert ok.status_code == 204
    assert (await ui.http.get(url, headers=AS_USER)).status_code == 404


async def test_list_filters_by_column_and_pages(ui: Board) -> None:
    for card in (cards.API_SEARCH, cards.INFRA_BACKUP, cards.RUN_COST):
        await ui.create(card)
    triaged = await ui.http.get("/api/tasks", params={"column": "triaged"}, headers=AS_USER)
    assert {item["column"] for item in triaged.json()["items"]} == {"triaged"}
    first = (await ui.http.get("/api/tasks", params={"limit": 2}, headers=AS_USER)).json()
    assert len(first["items"]) == 2
    rest = await ui.http.get(
        "/api/tasks", params={"limit": 2, "cursor": first["next_cursor"]}, headers=AS_USER
    )
    assert len(rest.json()["items"]) == 1
    assert "next_cursor" not in rest.json()
    bad = await ui.http.get("/api/tasks", params={"cursor": "!!"}, headers=AS_USER)
    assert bad.status_code == 400


async def test_stale_move_is_409_before_any_rule(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    response = await ui.move(task | {"version": 1}, "done")
    assert response.status_code == 409


async def test_maintainer_only_moves_are_403_for_users(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    response = await ui.move(task, "plan_review", headers=AS_USER)
    assert response.status_code == 403


async def test_reorder_with_a_taken_rank_keeps_ranks_unique(ui: Board) -> None:
    first = await ui.create(cards.API_SEARCH)
    second = await ui.create(cards.RUN_COST)
    response = await ui.move(second, "triaged", rank=str(first["rank"]))
    assert response.status_code == 200
    assert response.json()["rank"] != first["rank"]
    assert response.json()["rank"] > first["rank"]


async def test_blocking_needs_a_reason_and_returns_to_its_column(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    assert (await ui.move(task, "blocked")).status_code == 422
    blocked = await ui.move(task, "blocked", reason="Waiting on design")
    assert blocked.json()["blocked_reason"] == "Waiting on design"
    back = await ui.move(blocked.json(), "triaged")
    assert back.status_code == 200
    assert "blocked_reason" not in back.json()


async def test_retriage_only_for_early_columns(ui: Board) -> None:
    task = await ui.create(cards.INFRA_BACKUP)
    queued = await ui.http.post(f"/api/tasks/{task['id']}/triage", headers=AS_USER)
    assert queued.status_code == 202
    assert "run_id" in queued.json()
    await ui.settle()
    blocked = await ui.move(await ui.task(str(task["id"])), "blocked", reason="hold")
    again = await ui.http.post(f"/api/tasks/{blocked.json()['id']}/triage", headers=AS_USER)
    assert again.status_code == 422


async def test_plan_requests_are_maintainer_only(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    response = await ui.http.post(f"/api/tasks/{task['id']}/plan", headers=AS_USER)
    assert response.status_code == 403


async def test_a_confirmed_urgent_card_goes_on_to_plan_review_and_gets_a_plan(ui: Board) -> None:
    task = await ui.create(cards.URGENT_FIX)
    assert task["column"] == "plan_review"
    assert (task["priority"], task["route"]) == ("P1", "now")
    plan = await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_MAINTAINER)
    assert plan.status_code == 200
    assert plan.json()["acceptance_criteria"]


async def test_the_testers_urgent_card_also_goes_on_to_plan_review(ui: Board) -> None:
    # Triage routes the maintainer's and the tester's cards the same way.
    task = await ui.create(cards.URGENT_FIX, headers=AS_USER)
    assert (task["column"], task["route"]) == ("plan_review", "now")
    plan = await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_MAINTAINER)
    assert plan.status_code == 200


# The board colours a column red when the dragged card can't go there, so each card carries
# the columns the caller may move it to, straight from the server's state machine.
async def test_cards_carry_the_columns_the_caller_may_move_them_to(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)  # lands in triaged
    assert task["column"] == "triaged"
    as_maintainer = (await ui.http.get(f"/api/tasks/{task['id']}", headers=AS_MAINTAINER)).json()
    as_user = (await ui.http.get(f"/api/tasks/{task['id']}", headers=AS_USER)).json()
    assert "plan_review" in as_maintainer["allowed_columns"]
    assert "plan_review" not in as_user["allowed_columns"]  # maintainer-only move
    for allowed in (as_maintainer["allowed_columns"], as_user["allowed_columns"]):
        assert "triaged" not in allowed  # never its own column
        assert "done" not in allowed
        assert "agent_dev" not in allowed


async def test_the_board_list_carries_allowed_columns_too(ui: Board) -> None:
    await ui.create(cards.API_SEARCH)
    page = (await ui.http.get("/api/tasks", headers=AS_MAINTAINER)).json()
    assert all("allowed_columns" in item for item in page["items"])
