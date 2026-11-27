"""Request building and response parsing for Claude tool-use turns (the manual loop).

Client tools are sent with `strict: true` so arguments always fit their schema; `tool_choice`
stays `auto` (forcing a tool returns a 400 on the newest models) with parallel calls off, so a
turn holds at most one call. Web search is the server tool `web_search_20250305`, the variant
Vertex offers; its results come back inside the same turn.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

import anthropic
from anthropic.types.beta import (
    BetaContentBlock,
    BetaMessageParam,
    BetaToolChoiceAutoParam,
    BetaToolUnionParam,
)

from app.adapters.llm.providers.base import (
    ProviderCall,
    ProviderError,
    ProviderToolRequest,
    ProviderTurn,
    ProviderUsage,
)
from app.domain.ports import ServerToolUse, ToolCall, ToolOutput

WEB_SEARCH_TYPE = "web_search_20250305"
TOOL_CHOICE: BetaToolChoiceAutoParam = {"type": "auto", "disable_parallel_tool_use": True}

type Json = dict[str, object]


def tool_params(request: ProviderToolRequest) -> list[BetaToolUnionParam]:
    tools: list[Json] = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": anthropic.transform_schema(dict(tool.input_schema)),
            "strict": True,
        }
        for tool in request.tools
    ]
    for tool in request.server_tools:
        if tool.name == "web_search":
            search: Json = {
                "type": WEB_SEARCH_TYPE,
                "name": "web_search",
                "max_uses": tool.max_uses,
            }
            tools.append(search)
    return cast("list[BetaToolUnionParam]", tools)


def tool_messages(request: ProviderToolRequest) -> list[BetaMessageParam]:
    messages: list[Json] = [{"role": "user", "content": request.user}]
    for item in request.transcript:
        if isinstance(item, ToolOutput):
            result: Json = {
                "type": "tool_result",
                "tool_use_id": item.call_id,
                "content": item.content,
                "is_error": item.is_error,
            }
            messages.append({"role": "user", "content": [result]})
        else:
            messages.append({"role": "assistant", "content": _assistant_content(item)})
    return cast("list[BetaMessageParam]", messages)


def assistant_message(blocks: Sequence[BetaContentBlock]) -> BetaMessageParam:
    return cast("BetaMessageParam", {"role": "assistant", "content": dump_blocks(blocks)})


def dump_blocks(blocks: Sequence[BetaContentBlock]) -> list[Json]:
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


def _assistant_content(call: ToolCall) -> list[Json]:
    """The turn exactly as Claude produced it (thinking blocks must come back unchanged), or
    rebuilt from the call when it came from replay or another provider."""
    if call.provider_state:
        state: object = json.loads(call.provider_state)
        if isinstance(state, list):
            return cast("list[Json]", state)
    rebuilt: list[Json] = [{"type": "text", "text": call.text}] if call.text else []
    rebuilt.append(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.arguments)}
    )
    return rebuilt


def parse_turn(
    blocks: Sequence[BetaContentBlock], model: str, usage: ProviderUsage
) -> ProviderTurn:
    text = "".join(block.text for block in blocks if block.type == "text")
    uses = [block for block in blocks if block.type == "tool_use"]
    call = ProviderCall(uses[0].id, uses[0].name, _arguments(uses[0].input)) if uses else None
    return ProviderTurn(
        text=text,
        model=model,
        call=call,
        usage=usage,
        server_uses=_server_uses(blocks),
        state=json.dumps(dump_blocks(blocks)),
    )


def _arguments(raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ProviderError("invalid_tool_input") from None
    if not isinstance(raw, dict):
        raise ProviderError("invalid_tool_input")
    return cast("dict[str, object]", raw)


def _server_uses(blocks: Sequence[BetaContentBlock]) -> tuple[ServerToolUse, ...]:
    queries: dict[str, str] = {}
    found: list[ServerToolUse] = []
    for block in blocks:
        if block.type == "server_tool_use" and block.name == "web_search":
            query = _arguments(block.input).get("query")
            queries[block.id] = query if isinstance(query, str) else ""
        elif block.type == "web_search_tool_result":
            results = block.content if isinstance(block.content, list) else []
            found.append(
                ServerToolUse(
                    name="web_search",
                    query=queries.get(block.tool_use_id, ""),
                    titles=tuple(result.title for result in results),
                    sources=tuple(result.url for result in results),
                )
            )
    return tuple(found)


def add_usage(total: ProviderUsage, more: ProviderUsage) -> ProviderUsage:
    return ProviderUsage(
        input_tokens=total.input_tokens + more.input_tokens,
        output_tokens=total.output_tokens + more.output_tokens,
        cache_read_tokens=total.cache_read_tokens + more.cache_read_tokens,
    )
