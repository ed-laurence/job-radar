"""Claude tool-use request building and response parsing, without any network."""

from __future__ import annotations

import json

import pytest
from anthropic.types.beta import BetaContentBlock, BetaMessage

from app.adapters.llm.providers.base import ProviderError, ProviderToolRequest, ProviderUsage
from app.adapters.llm.providers.vertex_claude_tools import (
    TOOL_CHOICE,
    WEB_SEARCH_TYPE,
    parse_turn,
    tool_messages,
    tool_params,
)
from app.domain.ports import ServerTool, ToolCall, ToolOutput, ToolSpec

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"topic": {"type": "string", "enum": ["roles", "contacts"]}},
    "required": ["topic"],
    "additionalProperties": False,
}


def _request(*transcript: ToolCall | ToolOutput, web: bool = False) -> ProviderToolRequest:
    return ProviderToolRequest(
        feature="research",
        prompt_hash="0" * 32,
        model="claude-sonnet-5-5",
        effort="high",
        system="s",
        user="Research Acme.",
        max_tokens=1000,
        tools=(ToolSpec("lookup_known_data", "Look it up.", SCHEMA),),
        server_tools=(ServerTool("web_search", max_uses=2),) if web else (),
        transcript=transcript,
        answer_schema={"type": "object"},
    )


def _blocks(*raw: dict[str, object]) -> list[BetaContentBlock]:
    message = BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": list(raw),
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    return list(message.content)


def test_client_tools_are_strict_and_parallel_calls_are_off() -> None:
    params = [dict(param) for param in tool_params(_request(web=True))]
    assert params[0]["name"] == "lookup_known_data"
    assert params[0]["strict"] is True
    assert params[1] == {"type": WEB_SEARCH_TYPE, "name": "web_search", "max_uses": 2}
    assert TOOL_CHOICE == {"type": "auto", "disable_parallel_tool_use": True}


def test_transcript_becomes_assistant_tool_use_and_user_tool_result() -> None:
    call = ToolCall("toolu_1", "lookup_known_data", {"topic": "roles"}, text="Checking.")
    output = ToolOutput("toolu_1", '<data name="tool_result">\nTwo roles.\n</data>')
    messages = [dict(message) for message in tool_messages(_request(call, output))]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == [
        {"type": "text", "text": "Checking."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "lookup_known_data",
            "input": {"topic": "roles"},
        },
    ]
    assert messages[2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": output.content,
            "is_error": False,
        }
    ]


def test_provider_state_goes_back_unchanged() -> None:
    state = [{"type": "thinking", "thinking": "hmm", "signature": "sig"}, {"type": "tool_use"}]
    call = ToolCall("toolu_1", "lookup_known_data", {}, provider_state=json.dumps(state))
    messages = [dict(message) for message in tool_messages(_request(call))]
    assert messages[1]["content"] == state


def test_parse_a_tool_call_with_web_search_activity() -> None:
    blocks = _blocks(
        {"type": "text", "text": "Searching first."},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "Acme hiring"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "title": "Acme careers",
                    "url": "https://acme.example.test/careers",
                    "encrypted_content": "x",
                    "page_age": None,
                }
            ],
        },
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "lookup_known_data",
            "input": {"topic": "roles"},
        },
    )
    turn = parse_turn(blocks, "claude-sonnet-5-5", ProviderUsage(3, 4))
    assert turn.text == "Searching first."
    assert turn.call is not None
    assert (turn.call.id, turn.call.arguments) == ("toolu_2", {"topic": "roles"})
    assert turn.server_uses[0].query == "Acme hiring"
    assert turn.server_uses[0].sources == ("https://acme.example.test/careers",)
    assert turn.state is not None
    assert [block["type"] for block in json.loads(turn.state)][-1] == "tool_use"


def test_final_text_has_no_call() -> None:
    turn = parse_turn(_blocks({"type": "text", "text": '{"a": 1}'}), "m", ProviderUsage())
    assert turn.call is None
    assert turn.text == '{"a": 1}'


def test_tool_input_that_is_not_an_object_is_a_provider_error() -> None:
    blocks = _blocks({"type": "tool_use", "id": "t", "name": "lookup_known_data", "input": {}})
    object.__setattr__(blocks[0], "input", "[1, 2]")
    with pytest.raises(ProviderError):
        parse_turn(blocks, "m", ProviderUsage())
