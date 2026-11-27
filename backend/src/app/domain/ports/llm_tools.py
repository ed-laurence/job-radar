"""Tool use through the LLM port (ARCH-20): one model turn at a time.

The calling agent owns the loop (the manual-loop pattern), so it can persist every step: it
asks for the next action, runs the tool it is given, appends the call and its output to the
transcript and asks again, until the model gives its final answer. Tool outputs are untrusted
data (SEC-04): the adapter guards them before they reach a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    """A client tool the agent runs. `input_schema` is a strict JSON schema: every property
    is required and `additionalProperties` is false (Pydantic `extra="forbid"` gives that)."""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True)
class ServerTool:
    """A tool the provider runs inside the turn (web search). Only `web_search` exists."""

    name: str
    max_uses: int = 3


@dataclass(frozen=True)
class ServerToolUse:
    """What a server tool did during a turn, for the agent's step stream."""

    name: str
    query: str
    titles: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnUsage:
    """What the turn cost, so the agent can enforce its own per-run budget."""

    provider: str
    model: str
    cost_usd: float
    is_fallback: bool


@dataclass(frozen=True)
class ToolCall:
    """The model asked for one client tool. `arguments` are re-identified (real values).

    `provider_state` is opaque to everyone but the adapter that produced it (for Claude: the
    turn's content blocks, thinking included, which must go back unchanged)."""

    id: str
    name: str
    arguments: Mapping[str, object]
    text: str = ""
    server_uses: tuple[ServerToolUse, ...] = ()
    provider_state: str | None = None
    usage: TurnUsage | None = None


@dataclass(frozen=True)
class ToolOutput:
    """What the agent's tool returned for `call_id`. Untrusted: guarded before the next turn."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class FinalAnswer[T: BaseModel]:
    """The model finished: its answer, validated by the agent's answer model (ARCH-21)."""

    value: T
    server_uses: tuple[ServerToolUse, ...] = ()
    usage: TurnUsage | None = None


type TranscriptItem = ToolCall | ToolOutput
type AnyTool = ToolSpec | ServerTool


@dataclass(frozen=True)
class ToolTurn:
    """The tools on offer and the conversation so far (after the opening request)."""

    tools: tuple[AnyTool, ...]
    transcript: tuple[TranscriptItem, ...] = field(default=())
