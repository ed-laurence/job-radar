"""A provider route with no backend configured (e.g. Model Garden without a GCP project).

Every call fails as a `ProviderError`, so the LLM service moves to the route's fallback model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters.llm.providers.base import (
    ProviderChunk,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)


class UnconfiguredProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise ProviderError("not_configured")

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        del request
        raise ProviderError("not_configured")
