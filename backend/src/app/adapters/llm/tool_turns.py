"""One tool-use turn through the same pipeline as every other LLM call (ARCH-20/21, SEC-04/05).

Per turn: daily budget -> input guard on the opening data and on every tool output in the
transcript (untrusted, fail closed) -> PII de-identification of everything sent -> provider ->
output guard on the turn's text and tool arguments -> validation (a known tool, or a final
answer that fits the answer model; one retry, then the fallback, then `ValidationFailed`) ->
re-identification -> one `llm_calls` row per attempt.
"""

from __future__ import annotations

import dataclasses
import json
from typing import cast

from pydantic import BaseModel

from app.adapters.llm.ledger import Attempt, Call, Ledger, next_fallback, plan
from app.adapters.llm.outputs import JsonNode, refusal_fallback, reidentify_node, validate_json
from app.adapters.llm.prompts import (
    RenderedPrompt,
    render_tool_output,
    render_tool_prompt,
    transcript_hash,
)
from app.adapters.llm.providers.base import (
    ProviderError,
    ProviderToolRequest,
    ProviderTurn,
    ToolProvider,
)
from app.adapters.llm.routing import FeatureRoute, RoutingTable
from app.domain.errors import GuardBlocked, ValidationFailed
from app.domain.ports import (
    PII,
    ContentGuard,
    FinalAnswer,
    LlmRequest,
    ServerTool,
    ToolCall,
    ToolOutput,
    ToolSpec,
    ToolTurn,
    TranscriptItem,
    TurnUsage,
)

type Action[T: BaseModel] = ToolCall | FinalAnswer[T]


@dataclasses.dataclass(frozen=True)
class _Prepared:
    """The de-identified turn: what every attempt sends."""

    prompt: RenderedPrompt
    tools: tuple[ToolSpec, ...]
    server_tools: tuple[ServerTool, ...]
    transcript: tuple[TranscriptItem, ...]
    answer_schema: dict[str, object]
    prompt_hash: str


class ToolTurns:
    def __init__(
        self, routing: RoutingTable, ledger: Ledger, guard: ContentGuard, pii: PII
    ) -> None:
        self._routing = routing
        self._ledger = ledger
        self._guard = guard
        self._pii = pii

    async def next_action[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> Action[T]:
        route = self._routing.route(request.feature)
        prepared = await self._prepare(request, turn, answer)
        attempts = plan(route)
        index = 0
        while index < len(attempts):
            attempt = attempts[index]
            await self._ledger.check_budget(request)
            outcome = await self._attempt(request, attempt, prepared, route)
            if isinstance(outcome, ProviderError):
                index = next_fallback(attempts, index)
                continue
            provider_turn, call = outcome
            action = await self._action(provider_turn, prepared, answer)
            cost = await self._ledger.log(
                call,
                provider_turn.model,
                provider_turn.usage,
                is_ok=action is not None,
                error_code=None if action is not None else "invalid_output",
            )
            if action is not None:
                is_fallback = attempt.is_fallback or provider_turn.model != attempt.choice.model
                usage = TurnUsage(call.provider, provider_turn.model, cost, is_fallback)
                return dataclasses.replace(action, usage=usage)
            index += 1
        raise ValidationFailed("The model's answer could not be used. Try again later.")

    async def _prepare[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> _Prepared:
        await self._guard_inputs(request, turn)
        instructions = await self._pii.deidentify(request.instructions)
        blocks = [(block.name, await self._pii.deidentify(block.text)) for block in request.data]
        prompt = render_tool_prompt(request.system, instructions, blocks, answer.__name__)
        tools = tuple(tool for tool in turn.tools if isinstance(tool, ToolSpec))
        server_tools = tuple(tool for tool in turn.tools if isinstance(tool, ServerTool))
        transcript = tuple([await self._deidentify(item) for item in turn.transcript])
        key = transcript_hash(request.feature.value, prompt, turn.tools, transcript)
        schema = answer.model_json_schema()
        return _Prepared(prompt, tools, server_tools, transcript, schema, key)

    async def _guard_inputs(self, request: LlmRequest, turn: ToolTurn) -> None:
        """Every untrusted text, every turn: the transcript is the caller's, so nothing in it
        is assumed to have been checked before."""
        outputs = [item.content for item in turn.transcript if isinstance(item, ToolOutput)]
        for text in [block.text for block in request.data] + outputs:
            verdict = await self._guard.inspect_input(text)
            if verdict.is_blocked:
                await self._ledger.log_guard_block(request)
                raise GuardBlocked("This content can't be sent to the model.")

    async def _deidentify(self, item: TranscriptItem) -> TranscriptItem:
        if isinstance(item, ToolOutput):
            content = render_tool_output(await self._pii.deidentify(item.content))
            return ToolOutput(item.call_id, content, item.is_error)
        arguments = {
            key: await self._pii.deidentify(value) if isinstance(value, str) else value
            for key, value in item.arguments.items()
        }
        text = await self._pii.deidentify(item.text)
        return dataclasses.replace(item, arguments=arguments, text=text, usage=None)

    async def _attempt(
        self, request: LlmRequest, attempt: Attempt, prepared: _Prepared, route: FeatureRoute
    ) -> tuple[ProviderTurn, Call] | ProviderError:
        provider = self._ledger.provider(attempt)
        call = self._ledger.start(request, attempt)
        if not isinstance(provider, ToolProvider):
            await self._ledger.log_failure(call, "tools_unsupported")
            return ProviderError("tools_unsupported")
        provider_request = ProviderToolRequest(
            feature=request.feature.value,
            prompt_hash=prepared.prompt_hash,
            model=attempt.choice.model,
            effort=attempt.choice.effort,
            system=prepared.prompt.system,
            user=prepared.prompt.user,
            max_tokens=request.max_tokens or self._routing.max_tokens(request.feature),
            tools=prepared.tools,
            server_tools=prepared.server_tools,
            transcript=prepared.transcript,
            answer_schema=prepared.answer_schema,
            refusal_fallback_model=refusal_fallback(attempt, route),
        )
        try:
            provider_turn = await provider.next_turn(provider_request)
        except ProviderError as error:
            await self._ledger.log_failure(call, error.code)
            return error
        if await self._is_output_blocked(provider_turn):
            await self._ledger.log(
                call,
                provider_turn.model,
                provider_turn.usage,
                is_ok=False,
                error_code="guard_blocked",
                guard="block",
            )
            raise GuardBlocked("The model's answer was withheld by the safety filter.")
        return provider_turn, call

    async def _is_output_blocked(self, provider_turn: ProviderTurn) -> bool:
        parts = [provider_turn.text]
        if provider_turn.call is not None:
            parts.append(json.dumps(provider_turn.call.arguments, ensure_ascii=False))
        visible = "\n".join(part for part in parts if part.strip())
        if not visible:
            return False
        return (await self._guard.inspect_output(visible)).is_blocked

    async def _action[T: BaseModel](
        self, provider_turn: ProviderTurn, prepared: _Prepared, answer: type[T]
    ) -> Action[T] | None:
        """A call to a tool on offer, or an answer that validates. Anything else is unusable."""
        server_uses = provider_turn.server_uses
        call = provider_turn.call
        if call is not None:
            if call.name not in {tool.name for tool in prepared.tools}:
                return None
            restored = await reidentify_node(self._pii, cast("JsonNode", call.arguments))
            if not isinstance(restored, dict):
                return None
            text = await self._pii.reidentify(provider_turn.text)
            arguments = cast("dict[str, object]", restored)
            return ToolCall(call.id, call.name, arguments, text, server_uses, provider_turn.state)
        value = validate_json(provider_turn.text, answer)
        if value is None:
            return None
        dumped = cast("JsonNode", value.model_dump(mode="json"))
        restored_value = answer.model_validate(await reidentify_node(self._pii, dumped))
        return FinalAnswer(restored_value, server_uses)
