"""The task-chat tool loop through the real LLM adapter with scripted Claude turns: what the
model may reach, how bad calls come back to it, PII in proposals, and the turn cap."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace

from app.adapters.llm.providers.base import (
    ProviderCall,
    ProviderChunk,
    ProviderRequest,
    ProviderResponse,
    ProviderToolRequest,
    ProviderTurn,
    ProviderUsage,
)
from app.adapters.llm.providers.replay import ReplayProvider
from app.adapters.llm.routing import load_routing
from app.adapters.llm.service import LlmService
from app.agents.task_chat.agent import MAX_TURNS, OUT_OF_TURNS
from app.domain.ports import ToolOutput
from app.main import Container
from tests.unit.devboard import cards
from tests.unit.devboard.support import AS_MAINTAINER, Board, board
from tests.unit.devboard.test_task_chat_api import sse

MODEL = "claude-sonnet-5-5"


class ChatScript:
    """Scripted task-chat turns (triage still replays its fixtures); records each request."""

    name = "vertex-claude"

    def __init__(self, *turns: ProviderTurn) -> None:
        self.turns = list(turns)
        self.requests: list[ProviderToolRequest] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise AssertionError(f"unexpected {request.feature} call")

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        raise AssertionError(f"unexpected {request.feature} stream")
        yield ProviderChunk()

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn:
        self.requests.append(request)
        return self.turns.pop(0)


def _call(number: int, name: str, arguments: dict[str, object]) -> ProviderTurn:
    call = ProviderCall(f"toolu_{number}", name, arguments)
    return ProviderTurn(text="", model=MODEL, call=call, usage=ProviderUsage(10, 10))


def _answer(text: str) -> ProviderTurn:
    return ProviderTurn(json.dumps({"reply": text}), MODEL, usage=ProviderUsage(10, 10))


def _proposal(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": None,
        "description": None,
        "acceptance_criteria": None,
        "reason": "Clearer.",
    }
    return base | fields


async def _scripted(container: Container, script: ChatScript) -> AsyncGenerator[Board]:
    replay = ReplayProvider(container.settings.llm_fixtures_dir, on_miss=script)
    llm = LlmService(
        load_routing(container.settings.models_config),
        {"vertex-claude": replay, "vertex-maas": replay, "vertex-gemini": replay},
        container.guard,
        container.pii,
        container.usage,
        container.clock,
    )
    async with board(replace(container, llm=llm)) as value:
        yield value


async def _chat(ui: Board) -> tuple[str, list[tuple[str, dict[str, object]]]]:
    task = await ui.create(cards.API_SEARCH)
    response = await ui.http.post(
        f"/api/tasks/{task['id']}/chat", json={"content": "Tidy this up."}, headers=AS_MAINTAINER
    )
    assert response.status_code == 200, response.text
    return str(task["id"]), sse(response.text)


def _outputs(request: ProviderToolRequest) -> list[ToolOutput]:
    return [item for item in request.transcript if isinstance(item, ToolOutput)]


async def test_bad_calls_come_back_as_errors_and_only_one_proposal_is_kept(
    container: Container,
) -> None:
    script = ChatScript(
        _call(1, "read_repository", {"path": "infra/"}),
        _call(2, "propose_task_edit", {"title": "No reason given"}),
        _call(3, "propose_task_edit", _proposal(title="Search contacts; ask ops@example.test")),
        _call(4, "propose_task_edit", _proposal(title="A second try")),
        _answer("Done."),
    )
    async for ui in _scripted(container, script):
        task_id, events = await _chat(ui)
        steps = [data for name, data in events if name == "step"]
        assert [step["summary"] for step in steps] == [
            "Asked for a tool that doesn't exist.",
            "Couldn't propose the edit: Invalid input: pass title, description, "
            "acceptance_criteria and reason.",
            "Proposed an edit to the title.",
            "Couldn't propose the edit: You already proposed an edit in this reply.",
        ]
        last = _outputs(script.requests[-1])
        assert [output.is_error for output in last] == [True, True, False, True]
        history = (await ui.http.get(f"/api/tasks/{task_id}/chat", headers=AS_MAINTAINER)).json()
        title = history["items"][-1]["proposal"]["changes"]["title"]["after"]
        assert "ops@example.test" not in title
        assert "[redacted]" in title
        card = await ui.task(task_id)
        assert card["title"] == cards.API_SEARCH.title


async def test_the_model_is_offered_only_this_cards_tools(container: Container) -> None:
    script = ChatScript(_answer("Looks fine."))
    async for ui in _scripted(container, script):
        await _chat(ui)
        offered = {tool.name for tool in script.requests[0].tools}
        assert offered == {"get_task", "get_triage", "get_plan", "propose_task_edit"}
        assert script.requests[0].server_tools == ()
        assert "<data" in script.requests[0].user
        assert cards.API_SEARCH.body not in script.requests[0].system


async def test_a_reply_stops_at_the_turn_cap(container: Container) -> None:
    script = ChatScript(*[_call(n, "get_task", {}) for n in range(MAX_TURNS)])
    async for ui in _scripted(container, script):
        _, events = await _chat(ui)
        assert [name for name, _ in events].count("step") == MAX_TURNS
        reply = "".join(str(data["text"]) for name, data in events if name == "segment")
        assert reply.strip() == OUT_OF_TURNS
        assert events[-1][0] == "done"
