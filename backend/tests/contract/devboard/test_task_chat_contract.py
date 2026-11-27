"""Task chat against `api/openapi.yaml`: every operation is served where the spec says, bodies
and SSE events match the generated models, and the auth, rate-limit, size and 409 rules hold."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import cast

import pytest
import yaml
from tests.conftest import auth
from tests.unit.devboard import cards, chat_cards
from tests.unit.devboard.support import AS_MAINTAINER, AS_USER, Board, board
from tests.unit.devboard.test_task_chat_api import chat, proposal_of

from app.domain.devboard.model import RunKind, RunRecord, RunStatus
from app.generated.models import (
    TaskChatMessagePage,
    TaskChatProposal,
    TaskChatProposalAccepted,
    TaskChatStreamEvent,
)
from app.main import Container, build_app
from app.settings import Settings

TAG = "Task chat"
STRANGER = auth("stranger@example.test")  # signed in, but not on the allowlist: no edit rights
METHODS = {"get", "post", "delete"}


@pytest.fixture
async def ui(container: Container) -> AsyncGenerator[Board]:
    async with board(container) as value:
        yield value


def _spec_operations(settings: Settings) -> dict[str, tuple[str, str, dict[str, object]]]:
    spec = cast("dict[str, object]", yaml.safe_load(settings.openapi_path.read_text("utf-8")))
    found: dict[str, tuple[str, str, dict[str, object]]] = {}
    for path, item in cast("dict[str, dict[str, object]]", spec["paths"]).items():
        for method, raw in item.items():
            operation = cast("dict[str, object]", raw)
            if method in METHODS and TAG in cast("list[str]", operation.get("tags", [])):
                found[str(operation["operationId"])] = (method, "/api" + path, operation)
    return found


def _url(path: str, task_id: str, proposal_id: str = str(uuid.UUID(int=7))) -> str:
    return path.replace("{taskId}", task_id).replace("{proposalId}", proposal_id)


def test_every_task_chat_operation_is_served(container: Container) -> None:
    served = build_app(container).openapi()
    routes = {
        str(operation["operationId"]): (method, path)
        for path, item in served["paths"].items()
        for method, operation in item.items()
    }
    expected = _spec_operations(container.settings)
    assert set(expected) == {
        "listTaskChatMessages",
        "sendTaskChatMessage",
        "clearTaskChat",
        "acceptTaskChatProposal",
        "rejectTaskChatProposal",
    }
    for operation_id, (method, path, _) in expected.items():
        assert routes.get(operation_id) == (method, path), operation_id


def test_ai_routes_are_on_the_ai_tier(settings: Settings) -> None:
    tiers = {key: op["x-rate-limit-tier"] for key, (_, _, op) in _spec_operations(settings).items()}
    assert tiers["sendTaskChatMessage"] == "ai"
    assert tiers["acceptTaskChatProposal"] == "ai"


async def test_every_route_needs_a_signed_in_allowlisted_account(
    ui: Board, settings: Settings
) -> None:
    task = await ui.create(cards.API_SEARCH)
    body = {"content": "hi", "version": 1}
    for operation_id, (method, path, _) in _spec_operations(settings).items():
        url = _url(path, str(task["id"]))
        anonymous = await ui.http.request(method, url, json=body)
        assert anonymous.status_code == 401, operation_id
        refused = await ui.http.request(method, url, json=body, headers=STRANGER)
        assert refused.status_code == 403, operation_id
        assert refused.headers["content-type"].startswith("application/problem+json")


async def test_bodies_and_events_match_the_spec(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    task_id = str(task["id"])
    response = await ui.http.post(
        f"/api/tasks/{task_id}/chat", json={"content": chat_cards.MESSAGE}, headers=AS_MAINTAINER
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    names: list[str] = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        event = TaskChatStreamEvent.model_validate_json(
            f'{{"event": "{fields["event"]}", "id": "{fields["id"]}", "data": {fields["data"]}}}'
        )
        names.append(event.root.event)
    assert {"step", "segment", "done"} <= set(names)
    page = await ui.http.get(f"/api/tasks/{task_id}/chat", headers=AS_MAINTAINER)
    TaskChatMessagePage.model_validate(page.json())
    proposal = await proposal_of(ui, task_id)
    TaskChatProposal.model_validate(proposal)
    accepted = await ui.http.post(
        f"/api/tasks/{task_id}/chat/proposals/{proposal['id']}/accept",
        json={"version": task["version"]},
        headers=AS_MAINTAINER,
    )
    TaskChatProposalAccepted.model_validate(accepted.json())


async def test_a_stale_version_is_a_conflict_with_the_current_version(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    await chat(ui, str(task["id"]))
    proposal = await proposal_of(ui, str(task["id"]))
    stale = cast("int", task["version"]) - 1
    response = await ui.http.post(
        f"/api/tasks/{task['id']}/chat/proposals/{proposal['id']}/accept",
        json={"version": stale},
        headers=AS_MAINTAINER,
    )
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == "/problems/version-conflict"
    assert problem["current_version"] == task["version"]


async def test_a_proposal_is_stale_once_a_field_it_changes_was_edited(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    await chat(ui, str(task["id"]))
    proposal = await proposal_of(ui, str(task["id"]))
    # Someone else writes different criteria: the proposal's "before" no longer holds.
    rewritten = f"{cards.API_SEARCH.body}\n\n## Acceptance criteria\n- returns matches"
    edited = await ui.http.patch(
        f"/api/tasks/{task['id']}",
        json={"version": task["version"], "body": rewritten},
        headers=AS_USER,
    )
    await ui.settle()
    current = await ui.task(str(task["id"]))
    response = await ui.http.post(
        f"/api/tasks/{task['id']}/chat/proposals/{proposal['id']}/accept",
        json={"version": current["version"]},
        headers=AS_MAINTAINER,
    )
    assert edited.status_code == 200
    assert response.status_code == 409
    assert response.json()["current_version"] == current["version"]


async def test_no_edit_is_applied_while_an_agent_run_is_active(
    ui: Board, container: Container
) -> None:
    task = await ui.create(cards.API_SEARCH)
    await chat(ui, str(task["id"]))
    proposal = await proposal_of(ui, str(task["id"]))
    run = RunRecord(
        id=uuid.UUID(int=999),
        task_id=uuid.UUID(str(task["id"])),
        kind=RunKind.DEV,
        status=RunStatus.RUNNING,
        created_at=container.clock.now(),
    )
    await ui.runtime.stores.runs.insert(uuid.UUID(int=0), run)
    response = await ui.http.post(
        f"/api/tasks/{task['id']}/chat/proposals/{proposal['id']}/accept",
        json={"version": task["version"]},
        headers=AS_MAINTAINER,
    )
    assert response.status_code == 409
    problem = response.json()
    assert "agent run is active" in problem["detail"]
    assert "current_version" not in problem
    assert (await proposal_of(ui, str(task["id"])))["status"] == "pending"


async def test_messages_over_the_cap_are_refused(ui: Board) -> None:
    task = await ui.create(cards.API_SEARCH)
    url = f"/api/tasks/{task['id']}/chat"
    empty = await ui.http.post(url, json={"content": ""}, headers=AS_MAINTAINER)
    long = await ui.http.post(url, json={"content": "x" * 4001}, headers=AS_MAINTAINER)
    extra = await ui.http.post(url, json={"content": "hi", "role": "system"}, headers=AS_MAINTAINER)
    assert (empty.status_code, long.status_code, extra.status_code) == (422, 422, 422)


async def test_sending_is_rate_limited_at_the_ai_tier(ui: Board) -> None:
    missing = f"/api/tasks/{uuid.UUID(int=12345)}/chat"
    codes = [
        (await ui.http.post(missing, json={"content": "hi"}, headers=AS_USER)).status_code
        for _ in range(7)
    ]
    assert codes[:6] == [404] * 6
    assert codes[6] == 429
