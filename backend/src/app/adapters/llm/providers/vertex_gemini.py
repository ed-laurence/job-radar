"""Gemini on Vertex: interface only until the cross-model reviewer lands (M3).

Routing to it fails as a provider error, so the LLM service falls back to the route's fallback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters.llm.providers.base import (
    ProviderChunk,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)


class VertexGeminiProvider:
    name = "vertex-gemini"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise ProviderError("not_configured")

    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        del request
        raise ProviderError("not_configured")
