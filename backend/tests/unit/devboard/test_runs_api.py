"""Plan review gate, approval, simulated dev run, the step stream (SSE) and the kill switch."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest

from app.adapters.fakes.system import FakeClock
from app.compose.devboard import build
from app.domain.devboard.model import Actor, ActorKind, Column, MoveRequest, RunStatus
from app.domain.identity import AppRole, derive_user_id
from app.main import Container
from tests.conftest import MAINTAINER
from tests.unit.devboard import cards
from tests.unit.devboard.support import AS_MAINTAINER, AS_USER, Board, board


@pytest.fixture
async def ui(container: Container) -> AsyncGenerator[Board]:
    async with board(container) as value:
        yield value


def _events(body: str) -> list[tuple[str, str, dict[str, object]]]:
    frames: list[tuple[str, str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((fields["event"], fields["id"], json.loads(fields["data"])))
    return frames


async def _to_plan_review(ui: Board) -> dict[str, object]:
    task = await ui.create(cards.API_SEARCH)
    response = await ui.move(task, "plan_review")
    assert response.status_code == 200, response.text
    return await ui.task(str(task["id"]))


async def test_plan_review_runs_the_dev_lead_and_freezes_tests(ui: Board) -> None:
    task = await _to_plan_review(ui)
    plan = (await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_USER)).json()
    # Medium risk (from the api/domain paths) means a team: tests first, then the contract.
    assert plan["mode"] == "multi"
    assert [s["agent"] for s in plan["subtasks"]] == [
        "qa-expert",
        "backend-engineer",
        "backend-engineer",
    ]
    assert plan["subtasks"][1]["lease_paths"] == ["api/openapi.yaml"]
    assert plan["subtasks"][0]["agent"] == "qa-expert"
    assert plan["subtasks"][0]["test_only"] is True
    assert plan["acceptance_tests"] == [
        "backend/tests/unit/test_contacts_search.py::matches by name",
        "backend/tests/unit/test_contacts_search.py::orders by last contact",
    ]
    assert plan["risk"] == "high"  # the contract lease is a hotspot
    assert (plan["tests_approved"], plan["plan_approved"]) == (False, False)


async def test_ready_needs_approved_tests_and_plan(ui: Board) -> None:
    task = await _to_plan_review(ui)
    response = await ui.move(task, "ready")
    assert response.status_code == 422
    assert response.json()["type"] == "/problems/plan-not-approved"


async def test_approval_checks_both_versions_and_moves_to_ready(ui: Board) -> None:
    task = await _to_plan_review(ui)
    url = f"/api/tasks/{task['id']}/plan/approve"
    body = {
        "plan_version": 1,
        "task_version": task["version"],
        "approve_tests": True,
        "approve_plan": True,
    }
    stale_plan = await ui.http.post(url, json=body | {"plan_version": 9}, headers=AS_MAINTAINER)
    assert stale_plan.status_code == 409
    assert stale_plan.json()["current_version"] == 1
    stale_task = await ui.http.post(url, json=body | {"task_version": 1}, headers=AS_MAINTAINER)
    assert stale_task.status_code == 409
    assert (await ui.http.post(url, json=body, headers=AS_USER)).status_code == 403
    approved = await ui.http.post(url, json=body, headers=AS_MAINTAINER)
    assert approved.status_code == 200
    assert approved.json()["column"] == "ready"
    plan = (await ui.http.get(f"/api/tasks/{task['id']}/plan", headers=AS_USER)).json()
    assert plan["tests_approved"]
    assert plan["plan_approved"]
    assert "approved_at" in plan


async def _ready(ui: Board) -> dict[str, object]:
    task = await _to_plan_review(ui)
    body = {
        "plan_version": 1,
        "task_version": task["version"],
        "approve_tests": True,
        "approve_plan": True,
    }
    response = await ui.http.post(
        f"/api/tasks/{task['id']}/plan/approve", json=body, headers=AS_MAINTAINER
    )
    return response.json()


async def test_dev_run_streams_steps_and_lands_in_agent_review(ui: Board) -> None:
    task = await _ready(ui)
    assert (await ui.move(task, "agent_dev", headers=AS_USER)).status_code == 403
    assert (await ui.move(task, "agent_dev")).status_code == 200
    moved = await ui.task(str(task["id"]))
    assert moved["column"] == "agent_review"
    runs = (await ui.http.get(f"/api/tasks/{task['id']}/runs", headers=AS_USER)).json()["items"]
    assert [run["kind"] for run in runs] == ["dev", "plan", "triage"]
    dev = runs[0]
    assert dev["status"] == "succeeded"
    single = (await ui.http.get(f"/api/runs/{dev['id']}", headers=AS_USER)).json()
    assert single["id"] == dev["id"]

    stream = await ui.http.get(f"/api/runs/{dev['id']}/steps", headers=AS_USER)
    assert stream.headers["content-type"].startswith("text/event-stream")
    frames = _events(stream.text)
    kinds = [str(data.get("kind")) for event, _, data in frames if event == "step"]
    assert "lease" in kinds
    assert "handoff" in kinds
    assert frames[-1][0] == "done"
    assert frames[-1][2]["status"] == "succeeded"
    seqs = [int(seq) for event, seq, _ in frames if event == "step"]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1

    resumed = await ui.http.get(
        f"/api/runs/{dev['id']}/steps", headers=AS_USER | {"Last-Event-ID": str(seqs[-2])}
    )
    replay = [seq for event, seq, _ in _events(resumed.text) if event == "step"]
    assert replay == [str(seqs[-1])]
    assert await ui.runtime.leases.active(_maintainer()) == []


async def test_stream_of_unknown_run_is_404(ui: Board) -> None:
    response = await ui.http.get(
        "/api/runs/00000000-0000-4000-8000-999999999999/steps", headers=AS_USER
    )
    assert response.status_code == 404


async def test_bad_last_event_id_is_422(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    runs = (await ui.http.get(f"/api/tasks/{task['id']}/runs", headers=AS_USER)).json()["items"]
    response = await ui.http.get(
        f"/api/runs/{runs[0]['id']}/steps", headers=AS_USER | {"Last-Event-ID": "-1"}
    )
    assert response.status_code == 422


async def test_kill_switch_blocks_the_card_and_is_idempotent(ui: Board, clock: FakeClock) -> None:
    task = await _ready(ui)
    url = f"/api/tasks/{task['id']}/kill"
    assert (
        await ui.http.post(url, json={"version": task["version"]}, headers=AS_USER)
    ).status_code == 403
    stale = await ui.http.post(url, json={"version": 1}, headers=AS_MAINTAINER)
    assert stale.status_code == 409
    killed = await ui.http.post(
        url, json={"version": task["version"], "reason": "wrong approach"}, headers=AS_MAINTAINER
    )
    assert killed.status_code == 200
    assert killed.json()["column"] == "blocked"
    assert killed.json()["blocked_reason"] == "wrong approach"
    clock.advance(timedelta(minutes=1))  # the agent rate tier allows two calls a burst
    again = await ui.http.post(url, json={"version": 1}, headers=AS_MAINTAINER)
    assert again.status_code == 200
    assert again.json()["version"] == killed.json()["version"]


def _maintainer() -> Actor:
    return Actor(derive_user_id(MAINTAINER), ActorKind.MAINTAINER, AppRole.MAINTAINER)


async def test_kill_stops_a_running_dev_run_and_releases_its_leases(container: Container) -> None:
    gate = asyncio.Event()

    async def held(_seconds: float) -> None:
        await gate.wait()

    runtime = build(container, sleep=held)
    services, actor = runtime.services, _maintainer()
    task = await services.board.create(actor, cards.API_SEARCH.title, cards.API_SEARCH.body, ())
    await runtime.dispatcher.drain()
    task = await services.board.get(actor, task.id)
    task = await services.board.move(
        actor, task.id, MoveRequest(Column.PLAN_REVIEW, "U", task.version)
    )
    gate.set()
    await runtime.dispatcher.drain()
    gate.clear()
    task = await services.board.get(actor, task.id)
    task = await services.plans.approve(
        actor, task.id, plan_version=1, task_version=task.version, rank=None
    )
    task = await services.board.move(
        actor, task.id, MoveRequest(Column.AGENT_DEV, "U", task.version)
    )
    for _ in range(20):
        await asyncio.sleep(0)
    assert [lease.path for lease in await runtime.leases.active(actor)] == [
        "api/openapi.yaml",
        "backend/src/app/api/",
        "backend/src/app/domain/",
    ]
    task = await services.board.get(actor, task.id)
    killed = await services.workflow.kill(actor, task.id, task.version, None)
    await runtime.dispatcher.drain()
    assert killed.column is Column.BLOCKED
    assert killed.active_run_id is None
    assert await runtime.leases.active(actor) == []
    runs = await services.runs.for_task(actor, task.id, None, 10)
    assert runs.items[0].status is RunStatus.KILLED
    assert runtime.dispatcher.failures == []
