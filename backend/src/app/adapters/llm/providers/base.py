"""The provider interface inside `adapters.llm`. The domain never sees it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.domain.ports import ServerTool, ServerToolUse, ToolSpec, TranscriptItem


@dataclass(frozen=True)
class ProviderRequest:
    feature: str
    prompt_hash: str
    model: str
    effort: str
    system: str
    user: str
    max_tokens: int
    json_schema: dict[str, object] | None = None
    # Claude only: the model the client-side refusal middleware retries on.
    refusal_fallback_model: str | None = None


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)


@dataclass(frozen=True)
class ProviderChunk:
    """A text delta, or (last) the usage for the whole stream."""

    text: str = ""
    usage: ProviderUsage | None = None
    model: str | None = None


class ProviderError(Exception):
    """A provider call failed. `code` goes into `llm_calls.error_code`; no message text does."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderRefused(ProviderError):
    def __init__(self) -> None:
        super().__init__("refused")


class Provider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]: ...


@dataclass(frozen=True)
class ProviderToolRequest:
    """One tool-use turn. `user` is the rendered opening request; `transcript` holds the
    de-identified calls and data-wrapped tool outputs since then."""

    feature: str
    prompt_hash: str
    model: str
    effort: str
    system: str
    user: str
    max_tokens: int
    tools: tuple[ToolSpec, ...]
    server_tools: tuple[ServerTool, ...]
    transcript: tuple[TranscriptItem, ...]
    answer_schema: dict[str, object]
    refusal_fallback_model: str | None = None


@dataclass(frozen=True)
class ProviderCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ProviderTurn:
    """The model's turn: a tool call (with any accompanying text) or the final answer text."""

    text: str
    model: str
    call: ProviderCall | None = None
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    server_uses: tuple[ServerToolUse, ...] = ()
    state: str | None = None


@runtime_checkable
class ToolProvider(Protocol):
    """Providers that support tool use. Others fail over to the route's fallback."""

    name: str

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn: ...
