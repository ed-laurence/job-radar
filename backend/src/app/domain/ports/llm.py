"""The LLM port (ARCH-20/21/22) and the usage store behind spend logging and budgets."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from app.domain.ports.llm_tools import FinalAnswer, ToolCall, ToolTurn


class LlmFeature(StrEnum):
    SCORE = "score"
    EXTRACT = "extract"
    KIT = "kit"
    CHAT = "chat"
    RESEARCH = "research"
    TRIAGE = "triage"
    PLAN = "plan"
    DEV = "dev"
    REVIEW = "review"
    DIGEST = "digest"
    FOLLOWUP = "followup"
    OUTREACH = "outreach"
    REJECTION_ANALYSIS = "rejection_analysis"
    EVIDENCE = "evidence"
    EVAL = "eval"
    TASK_CHAT = "task_chat"


@dataclass(frozen=True)
class DataBlock:
    """Untrusted text (ads, task text, web results). Rendered as delimited data, never as
    instructions, and inspected by the content guard before it reaches a model."""

    name: str
    text: str


@dataclass(frozen=True)
class LlmAttribution:
    """Who pays for the call. Agent calls go to the maintainer who triggered them."""

    user_id: uuid.UUID
    role_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    research_run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class LlmRequest:
    feature: LlmFeature
    system: str
    instructions: str
    attribution: LlmAttribution
    data: tuple[DataBlock, ...] = field(default=())
    max_tokens: int | None = None


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    value: T
    provider: str
    model: str
    is_fallback: bool


class LLM(Protocol):
    async def generate[T: BaseModel](self, request: LlmRequest, output: type[T]) -> LlmResult[T]:
        """Structured output, validated by `output`. Retry once, then the fallback model, then
        `ValidationFailed`. Raises `GuardBlocked`, `BudgetExceeded` or `UpstreamUnavailable`."""
        ...

    def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        """Text deltas. Callers pass them through the streaming redactor before release."""
        ...

    async def next_action[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> ToolCall | FinalAnswer[T]:
        """One tool-use turn: the model either calls one tool or gives its final answer,
        validated by `answer`. The caller runs the tool and calls again with the transcript.
        Tool outputs pass the input guard and the turn's text the output guard (fail closed).
        Raises `GuardBlocked`, `BudgetExceeded`, `ValidationFailed` or `UpstreamUnavailable`."""
        ...


@dataclass(frozen=True)
class LlmCallRecord:
    """One provider attempt. Never holds prompts or outputs."""

    user_id: uuid.UUID
    at: datetime
    feature: LlmFeature
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_ms: int
    is_ok: bool
    is_fallback: bool
    guard_input: str
    guard_output: str | None
    error_code: str | None = None
    role_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    research_run_id: uuid.UUID | None = None


class LlmUsageStore(Protocol):
    async def record_call(self, record: LlmCallRecord) -> None: ...

    async def spent(self, day: date, feature: LlmFeature) -> float: ...

    async def add_spend(self, day: date, feature: LlmFeature, cost_usd: float) -> None: ...


class SpendCap(Protocol):
    async def check(self, user_id: uuid.UUID, now: datetime) -> None:
        """Raises `BudgetExceeded` when the user's monthly LLM budget is used up."""
        ...
