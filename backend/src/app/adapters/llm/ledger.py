"""Attempt planning, daily budgets, the monthly spend cap and `llm_calls` logging, shared by
every kind of LLM call.

Each provider attempt writes one `llm_calls` row (no prompts or outputs) and adds to spend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.adapters.llm.providers.base import Provider, ProviderUsage
from app.adapters.llm.routing import FeatureRoute, ModelChoice, RoutingTable
from app.domain.errors import BudgetExceeded, UpstreamUnavailable
from app.domain.ports import Clock, LlmCallRecord, LlmRequest, LlmUsageStore, SpendCap


@dataclass(frozen=True)
class Attempt:
    choice: ModelChoice
    is_fallback: bool


@dataclass(frozen=True)
class Outcome:
    """What one attempt cost and how it ended, for its `llm_calls` row."""

    model: str
    usage: ProviderUsage
    cost: float
    is_ok: bool
    error_code: str | None = None
    guard_input: str = "pass"
    guard_output: str | None = None


@dataclass(frozen=True)
class Call:
    """Everything needed to log one attempt."""

    request: LlmRequest
    attempt: Attempt
    provider: str
    started_ms: float


def plan(route: FeatureRoute) -> list[Attempt]:
    """The model twice (the second is the validation retry), then the fallback."""
    attempts = [Attempt(route, is_fallback=False), Attempt(route, is_fallback=False)]
    if route.fallback is not None:
        attempts.append(Attempt(route.fallback, is_fallback=True))
    return attempts


def next_fallback(attempts: list[Attempt], index: int) -> int:
    """A provider failure skips the same-model retry and goes straight to the fallback."""
    for position in range(index + 1, len(attempts)):
        if attempts[position].is_fallback:
            return position
    return len(attempts)


class Ledger:
    def __init__(
        self,
        routing: RoutingTable,
        providers: Mapping[str, Provider],
        usage: LlmUsageStore,
        clock: Clock,
    ) -> None:
        self._routing = routing
        self._providers = providers
        self._usage = usage
        self._clock = clock
        self.spend_cap: SpendCap | None = None

    def provider(self, attempt: Attempt) -> Provider:
        provider = self._providers.get(attempt.choice.provider)
        if provider is None:
            raise UpstreamUnavailable("The model is unavailable. Try again later.")
        return provider

    def start(self, request: LlmRequest, attempt: Attempt) -> Call:
        return Call(request, attempt, self.provider(attempt).name, self._millis())

    async def check_budget(self, request: LlmRequest) -> None:
        day = self._clock.now().date()
        spent = await self._usage.spent(day, request.feature)
        if spent >= self._routing.daily_budget(request.feature):
            raise BudgetExceeded("Today's AI budget for this feature is used up.")
        if self.spend_cap is not None:
            await self.spend_cap.check(request.attribution.user_id, self._clock.now())

    async def log(
        self,
        call: Call,
        model: str,
        usage: ProviderUsage,
        *,
        is_ok: bool,
        error_code: str | None = None,
        guard: str = "pass",
    ) -> float:
        """Log a completed attempt and return what it cost."""
        cost = self._routing.cost_usd(
            model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens
        )
        outcome = Outcome(
            model, usage, cost, is_ok=is_ok, error_code=error_code, guard_output=guard
        )
        await self._record(call, outcome)
        return cost

    async def log_failure(self, call: Call, error_code: str) -> None:
        outcome = Outcome(
            call.attempt.choice.model, ProviderUsage(), 0.0, is_ok=False, error_code=error_code
        )
        await self._record(call, outcome)

    async def log_guard_block(self, request: LlmRequest) -> None:
        route = self._routing.route(request.feature)
        call = self.start(request, Attempt(route, is_fallback=False))
        outcome = Outcome(
            route.model,
            ProviderUsage(),
            0.0,
            is_ok=False,
            error_code="guard_blocked",
            guard_input="block",
        )
        await self._record(call, outcome)

    def _millis(self) -> float:
        return self._clock.now().timestamp() * 1000

    async def _record(self, call: Call, outcome: Outcome) -> None:
        now = self._clock.now()
        attribution = call.request.attribution
        usage = outcome.usage
        await self._usage.record_call(
            LlmCallRecord(
                user_id=attribution.user_id,
                at=now,
                feature=call.request.feature,
                provider=call.provider,
                model=outcome.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cost_usd=outcome.cost,
                latency_ms=max(0, int(now.timestamp() * 1000 - call.started_ms)),
                is_ok=outcome.is_ok,
                is_fallback=call.attempt.is_fallback or outcome.model != call.attempt.choice.model,
                guard_input=outcome.guard_input,
                guard_output=outcome.guard_output,
                error_code=outcome.error_code,
                role_id=attribution.role_id,
                run_id=attribution.run_id,
                research_run_id=attribution.research_run_id,
            )
        )
        if outcome.cost > 0:
            await self._usage.add_spend(now.date(), call.request.feature, outcome.cost)
