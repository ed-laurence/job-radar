"""The LLM adapter behind `domain.ports.LLM` (ARCH-20/21/22).

Every call: route from `ai/models.yaml` -> daily budget check -> ContentGuard on untrusted
input -> PII de-identification -> provider -> ContentGuard on output -> Pydantic validation
(one retry, then the fallback model, then `ValidationFailed`) -> re-identification.
Each provider attempt writes one `llm_calls` row (see `ledger`). Tool-use turns follow the
same pipeline in `tool_turns`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import cast

from pydantic import BaseModel

from app.adapters.llm.ledger import Attempt, Call, Ledger, next_fallback, plan
from app.adapters.llm.outputs import JsonNode, refusal_fallback, reidentify_node, validate_json
from app.adapters.llm.prompts import RenderedPrompt, prompt_hash, render
from app.adapters.llm.providers.base import (
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from app.adapters.llm.routing import FeatureRoute, RoutingTable
from app.adapters.llm.tool_turns import ToolTurns
from app.domain.errors import GuardBlocked, UpstreamUnavailable, ValidationFailed
from app.domain.ports import (
    PII,
    Clock,
    ContentGuard,
    FinalAnswer,
    LlmRequest,
    LlmResult,
    LlmUsageStore,
    SpendCap,
    ToolCall,
    ToolTurn,
)


class LlmService:
    def __init__(
        self,
        routing: RoutingTable,
        providers: Mapping[str, Provider],
        guard: ContentGuard,
        pii: PII,
        usage: LlmUsageStore,
        clock: Clock,
    ) -> None:
        self._routing = routing
        self._guard = guard
        self._pii = pii
        self._ledger = Ledger(routing, providers, usage, clock)
        self._tools = ToolTurns(routing, self._ledger, guard, pii)

    def use_spend_cap(self, cap: SpendCap) -> None:
        """The per-user monthly budget (`llm.monthly_budget_aud`, against spend converted to
        AUD), checked with the daily one."""
        self._ledger.spend_cap = cap

    async def generate[T: BaseModel](self, request: LlmRequest, output: type[T]) -> LlmResult[T]:
        route = self._routing.route(request.feature)
        prompt = await self._prepare(request, output.__name__)
        schema = output.model_json_schema()
        attempts = plan(route)
        index = 0
        while index < len(attempts):
            attempt = attempts[index]
            await self._ledger.check_budget(request)
            outcome = await self._attempt(request, attempt, prompt, schema, route)
            if isinstance(outcome, ProviderError):
                index = next_fallback(attempts, index)
                continue
            response, call = outcome
            value = validate_json(response.text, output)
            error_code = None if value is not None else "invalid_output"
            await self._ledger.log(
                call, response.model, response.usage, is_ok=value is not None, error_code=error_code
            )
            if value is not None:
                restored = await self._reidentify(value, output)
                is_fallback = attempt.is_fallback or response.model != attempt.choice.model
                return LlmResult(restored, call.provider, response.model, is_fallback)
            index += 1
        raise ValidationFailed("The model's answer could not be used. Try again later.")

    async def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        route = self._routing.route(request.feature)
        prompt = await self._prepare(request, None)
        await self._ledger.check_budget(request)
        for attempt in plan(route)[::2]:
            provider = self._ledger.provider(attempt)
            call = self._ledger.start(request, attempt)
            provider_request = self._provider_request(request, attempt, prompt, None, route)
            emitted = False
            try:
                async for text in self._stream_once(provider, provider_request, call):
                    emitted = True
                    yield text
            except ProviderError as error:
                await self._ledger.log_failure(call, error.code)
                if emitted:
                    raise UpstreamUnavailable("The model stopped mid-answer.") from None
                continue
            return
        raise UpstreamUnavailable("The model is unavailable. Try again later.")

    async def next_action[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> ToolCall | FinalAnswer[T]:
        return await self._tools.next_action(request, turn, answer)

    async def _stream_once(
        self, provider: Provider, provider_request: ProviderRequest, call: Call
    ) -> AsyncIterator[str]:
        held = ""
        usage = ProviderUsage()
        model = provider_request.model
        async for chunk in provider.stream(provider_request):
            usage = chunk.usage or usage
            model = chunk.model or model
            held += chunk.text
            # PII tokens never contain whitespace, so text up to the last space is complete.
            cut = max(held.rfind(" "), held.rfind("\n")) + 1
            if cut > 0:
                ready, held = held[:cut], held[cut:]
                yield await self._pii.reidentify(ready)
        if held:
            yield await self._pii.reidentify(held)
        await self._ledger.log(call, model, usage, is_ok=True)

    async def _prepare(self, request: LlmRequest, schema_name: str | None) -> RenderedPrompt:
        for block in request.data:
            verdict = await self._guard.inspect_input(block.text)
            if verdict.is_blocked:
                await self._ledger.log_guard_block(request)
                raise GuardBlocked("This content can't be sent to the model.")
        instructions = await self._pii.deidentify(request.instructions)
        blocks = [(block.name, await self._pii.deidentify(block.text)) for block in request.data]
        return render(request.system, instructions, blocks, schema_name)

    async def _attempt(
        self,
        request: LlmRequest,
        attempt: Attempt,
        prompt: RenderedPrompt,
        schema: dict[str, object],
        route: FeatureRoute,
    ) -> tuple[ProviderResponse, Call] | ProviderError:
        provider = self._ledger.provider(attempt)
        call = self._ledger.start(request, attempt)
        provider_request = self._provider_request(request, attempt, prompt, schema, route)
        try:
            response = await provider.complete(provider_request)
        except ProviderError as error:
            await self._ledger.log_failure(call, error.code)
            return error
        verdict = await self._guard.inspect_output(response.text)
        if verdict.is_blocked:
            await self._ledger.log(
                call,
                response.model,
                response.usage,
                is_ok=False,
                error_code="guard_blocked",
                guard="block",
            )
            raise GuardBlocked("The model's answer was withheld by the safety filter.")
        return response, call

    def _provider_request(
        self,
        request: LlmRequest,
        attempt: Attempt,
        prompt: RenderedPrompt,
        schema: dict[str, object] | None,
        route: FeatureRoute,
    ) -> ProviderRequest:
        return ProviderRequest(
            feature=request.feature.value,
            prompt_hash=prompt_hash(request.feature.value, prompt),
            model=attempt.choice.model,
            effort=attempt.choice.effort,
            system=prompt.system,
            user=prompt.user,
            max_tokens=request.max_tokens or self._routing.max_tokens(request.feature),
            json_schema=schema,
            refusal_fallback_model=refusal_fallback(attempt, route),
        )

    async def _reidentify[T: BaseModel](self, value: T, output: type[T]) -> T:
        dumped = cast("JsonNode", value.model_dump(mode="json"))
        return output.model_validate(await reidentify_node(self._pii, dumped))
