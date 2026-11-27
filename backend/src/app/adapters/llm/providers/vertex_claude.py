"""Claude on Vertex AI through `AsyncAnthropicVertex`.

Vertex has no server-side refusal fallbacks, so each client registers the SDK's client-side
`BetaRefusalFallbackMiddleware` for the route's Claude fallback model. It only handles
`client.beta.messages`, which is why every call goes through the beta surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, cast

import anthropic
from anthropic import BetaRefusalFallbackMiddleware
from anthropic.lib.vertex import AsyncAnthropicVertex
from anthropic.types.beta import (
    BetaContentBlock,
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
)

from app.adapters.llm.providers.base import (
    ProviderChunk,
    ProviderError,
    ProviderRefused,
    ProviderRequest,
    ProviderResponse,
    ProviderToolRequest,
    ProviderTurn,
    ProviderUsage,
)
from app.adapters.llm.providers.vertex_claude_tools import (
    TOOL_CHOICE,
    add_usage,
    assistant_message,
    parse_turn,
    tool_messages,
    tool_params,
)

type EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
# Both clients expose the same Messages API surface (`client.beta.messages.*`).
type ClaudeClient = AsyncAnthropicVertex | anthropic.AsyncAnthropic
# A server-tool turn (web search) can pause; it is resumed this many times at most.
MAX_CONTINUATIONS = 3


class VertexClaudeProvider:
    name = "vertex-claude"

    def __init__(self, *, project_id: str, region: str, timeout_s: float = 120.0) -> None:
        self._project_id = project_id
        self._region = region
        self._timeout_s = timeout_s
        self._clients: dict[str | None, ClaudeClient] = {}

    def _client(self, fallback_model: str | None) -> ClaudeClient:
        client = self._clients.get(fallback_model)
        if client is None:
            middleware = (
                [BetaRefusalFallbackMiddleware([{"model": fallback_model}])]
                if fallback_model
                else None
            )
            client = self._build_client(middleware)
            self._clients[fallback_model] = client
        return client

    def _build_client(self, middleware: list[BetaRefusalFallbackMiddleware] | None) -> ClaudeClient:
        # Credentials come from ADC (Workload Identity); no keys, no env reads here.
        return AsyncAnthropicVertex(
            region=self._region,
            project_id=self._project_id,
            timeout=self._timeout_s,
            max_retries=2,
            middleware=middleware,
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        client = self._client(request.refusal_fallback_model)
        try:
            message = await client.beta.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                thinking={"type": "adaptive"},
                output_config=_output_config(request),
            )
        except anthropic.APITimeoutError:
            raise ProviderError("timeout") from None
        except anthropic.APIError:
            raise ProviderError("provider_error") from None
        return _to_response(message)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        client = self._client(request.refusal_fallback_model)
        try:
            async with client.beta.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                thinking={"type": "adaptive"},
                output_config=_output_config(request),
            ) as stream:
                async for text in stream.text_stream:
                    yield ProviderChunk(text=text)
                final = await stream.get_final_message()
        except anthropic.APITimeoutError:
            raise ProviderError("timeout") from None
        except anthropic.APIError:
            raise ProviderError("provider_error") from None
        if final.stop_reason == "refusal":
            raise ProviderRefused
        yield ProviderChunk(usage=_usage(final), model=final.model)

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn:
        """One tool-use turn. A paused server-tool turn is resumed by sending it back as is."""
        messages = tool_messages(request)
        blocks: list[BetaContentBlock] = []
        usage = ProviderUsage()
        for _ in range(MAX_CONTINUATIONS + 1):
            message = await self._create_turn(request, messages)
            if message.stop_reason == "refusal":
                raise ProviderRefused
            blocks += message.content
            usage = add_usage(usage, _usage(message))
            if message.stop_reason != "pause_turn":
                break
            messages = [*messages, assistant_message(message.content)]
        else:
            raise ProviderError("paused")
        has_call = any(block.type == "tool_use" for block in blocks)
        if message.stop_reason == "max_tokens" and has_call:
            # A cut-off call may parse as a valid partial object; never run it.
            raise ProviderError("max_tokens")
        return parse_turn(blocks, message.model, usage)

    async def _create_turn(
        self, request: ProviderToolRequest, messages: list[BetaMessageParam]
    ) -> BetaMessage:
        client = self._client(request.refusal_fallback_model)
        try:
            return await client.beta.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                tools=tool_params(request),
                tool_choice=TOOL_CHOICE,
                thinking={"type": "adaptive"},
                output_config=_config(request.effort, request.answer_schema),
            )
        except anthropic.APITimeoutError:
            raise ProviderError("timeout") from None
        except anthropic.APIError:
            raise ProviderError("provider_error") from None


def _output_config(request: ProviderRequest) -> BetaOutputConfigParam:
    return _config(request.effort, request.json_schema)


def _config(effort: str, json_schema: dict[str, object] | None) -> BetaOutputConfigParam:
    config: BetaOutputConfigParam = {"effort": cast("EffortLevel", effort)}
    if json_schema is not None:
        schema = anthropic.transform_schema(dict(json_schema))
        config["format"] = {"type": "json_schema", "schema": schema}
    return config


def _usage(message: BetaMessage) -> ProviderUsage:
    usage = message.usage
    return ProviderUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )


def _to_response(message: BetaMessage) -> ProviderResponse:
    if message.stop_reason == "refusal":
        raise ProviderRefused
    text = "".join(block.text for block in message.content if block.type == "text")
    return ProviderResponse(text=text, model=message.model, usage=_usage(message))


class AnthropicClaudeProvider(VertexClaudeProvider):
    """Local development only: the same Claude provider over the first-party Claude API.

    Identical requests and features; only authentication differs (an API key instead of ADC).
    Used when `CLAUDE_BACKEND=anthropic` (e.g. while Vertex quota is pending). The settings
    validator refuses this backend in prod, which always uses Vertex.
    """

    name = "vertex-claude"  # the route key stays the same, so ai/models.yaml is unchanged

    def __init__(self, *, api_key: str, timeout_s: float = 120.0) -> None:
        super().__init__(project_id="", region="", timeout_s=timeout_s)
        self._api_key = api_key

    def _build_client(self, middleware: list[BetaRefusalFallbackMiddleware] | None) -> ClaudeClient:
        return anthropic.AsyncAnthropic(
            api_key=self._api_key, timeout=self._timeout_s, max_retries=2, middleware=middleware
        )
