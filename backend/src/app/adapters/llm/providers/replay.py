"""Offline LLM (`LLM=replay`) and fixture recording (`LLM=record`, human-run only).

Fixtures live at `<fixtures>/<feature>/<prompt_hash>.json`. A tool-use run has one fixture per
turn, keyed by the hash of the whole transcript so far; a turn that called a tool records the
call in `tool_call`. An unknown hash raises
`ReplayFixtureMissing`, which is deliberately not a `ProviderError`: it must fail the test or
request loudly instead of quietly triggering the fallback model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.adapters.llm.providers.base import (
    Provider,
    ProviderCall,
    ProviderChunk,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderToolRequest,
    ProviderTurn,
    ProviderUsage,
    ToolProvider,
)
from app.domain.ports import ServerToolUse

REPLAY_CHUNK_CHARS = 24


class ReplayFixtureMissing(RuntimeError):
    def __init__(self, feature: str, prompt_hash: str) -> None:
        super().__init__(
            f"No LLM replay fixture for feature={feature} prompt_hash={prompt_hash}. "
            "Prompts changed? Re-record with LLM=record (human-run)."
        )
        self.feature = feature
        self.prompt_hash = prompt_hash


class FixtureCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object]


class FixtureServerUse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    query: str
    titles: list[str] = Field(default_factory=list[str])
    sources: list[str] = Field(default_factory=list[str])


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{32}$")
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call: FixtureCall | None = None
    server_uses: list[FixtureServerUse] = Field(default_factory=list[FixtureServerUse])

    def turn(self) -> ProviderTurn:
        call = self.tool_call
        return ProviderTurn(
            text=self.text,
            model=self.model,
            call=ProviderCall(call.id, call.name, dict(call.arguments)) if call else None,
            usage=ProviderUsage(input_tokens=self.input_tokens, output_tokens=self.output_tokens),
            server_uses=tuple(
                ServerToolUse(use.name, use.query, tuple(use.titles), tuple(use.sources))
                for use in self.server_uses
            ),
        )


def fixture_path(root: Path, feature: str, prompt_hash: str) -> Path:
    return root / feature / f"{prompt_hash}.json"


def fixture_roots(root: Path) -> tuple[Path, ...]:
    """The base fixture root plus each area folder (`<root>/<area>/<feature>/<hash>.json`)."""
    areas = sorted(child for child in root.iterdir() if child.is_dir()) if root.is_dir() else []
    return (root, *areas)


class ReplayProvider:
    name = "replay"

    def __init__(self, fixtures: Path, *, on_miss: Provider | None = None) -> None:
        # `on_miss` is for local dev only (LLM_REPLAY_MISS=synthesize); tests keep it None so
        # an unrecorded prompt still fails loudly.
        self._roots = fixture_roots(fixtures)
        self._on_miss = on_miss

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        fixture = await self._find(request)
        if fixture is None:
            if self._on_miss is None:
                raise ReplayFixtureMissing(request.feature, request.prompt_hash)
            return await self._on_miss.complete(request)
        usage = ProviderUsage(
            input_tokens=fixture.input_tokens, output_tokens=fixture.output_tokens
        )
        return ProviderResponse(text=fixture.text, model=fixture.model, usage=usage)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        fixture = await self._find(request)
        if fixture is None:
            if self._on_miss is None:
                raise ReplayFixtureMissing(request.feature, request.prompt_hash)
            async for chunk in self._on_miss.stream(request):
                yield chunk
            return
        for start in range(0, len(fixture.text), REPLAY_CHUNK_CHARS):
            yield ProviderChunk(text=fixture.text[start : start + REPLAY_CHUNK_CHARS])
        usage = ProviderUsage(
            input_tokens=fixture.input_tokens, output_tokens=fixture.output_tokens
        )
        yield ProviderChunk(usage=usage, model=fixture.model)

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn:
        fixture = await self._find(request)
        if fixture is not None:
            return fixture.turn()
        if self._on_miss is None:
            raise ReplayFixtureMissing(request.feature, request.prompt_hash)
        if not isinstance(self._on_miss, ToolProvider):
            raise ProviderError("tools_unsupported")
        return await self._on_miss.next_turn(request)

    async def _find(self, request: ProviderRequest | ProviderToolRequest) -> Fixture | None:
        for root in self._roots:
            path = fixture_path(root, request.feature, request.prompt_hash)
            try:
                raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            except FileNotFoundError:
                continue
            return Fixture.model_validate_json(raw)
        return None


class RecordingProvider:
    """Calls the real provider and writes what it returned as a replay fixture."""

    def __init__(self, inner: Provider, fixtures: Path) -> None:
        self.name = inner.name
        self._inner = inner
        self._fixtures = fixtures

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = await self._inner.complete(request)
        await self._write(request, response.text, response.model, response.usage)
        return response

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        parts: list[str] = []
        usage = ProviderUsage()
        model = request.model
        async for chunk in self._inner.stream(request):
            parts.append(chunk.text)
            usage = chunk.usage or usage
            model = chunk.model or model
            yield chunk
        await self._write(request, "".join(parts), model, usage)

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn:
        if not isinstance(self._inner, ToolProvider):
            raise ProviderError("tools_unsupported")
        turn = await self._inner.next_turn(request)
        call = turn.call
        await self._save(
            Fixture(
                feature=request.feature,
                prompt_hash=request.prompt_hash,
                model=turn.model,
                text=turn.text,
                input_tokens=turn.usage.input_tokens,
                output_tokens=turn.usage.output_tokens,
                tool_call=FixtureCall(id=call.id, name=call.name, arguments=call.arguments)
                if call
                else None,
                server_uses=[
                    FixtureServerUse(
                        name=use.name,
                        query=use.query,
                        titles=list(use.titles),
                        sources=list(use.sources),
                    )
                    for use in turn.server_uses
                ],
            )
        )
        return turn

    async def _write(
        self, request: ProviderRequest, text: str, model: str, usage: ProviderUsage
    ) -> None:
        fixture = Fixture(
            feature=request.feature,
            prompt_hash=request.prompt_hash,
            model=model,
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        await self._save(fixture)

    async def _save(self, fixture: Fixture) -> None:
        path = fixture_path(self._fixtures, fixture.feature, fixture.prompt_hash)
        # Tool fields only appear on tool-use turns, so plain fixtures keep their shape.
        dumped = fixture.model_dump(exclude={"tool_call", "server_uses"}, exclude_none=True)
        if fixture.tool_call is not None or fixture.server_uses:
            dumped = fixture.model_dump(exclude_none=True)
        body = json.dumps(dumped, indent=2, sort_keys=True) + "\n"
        await asyncio.to_thread(_write_file, path, body)


def _write_file(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
