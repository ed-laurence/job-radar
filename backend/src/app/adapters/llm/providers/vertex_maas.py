"""Open-weight models from Vertex AI Model Garden (serverless MaaS, pay per token).

Uses Vertex's OpenAI-compatible chat completions endpoint over httpx with ADC tokens.
Structured output is requested in the system prompt and validated by the LLM service, which
retries and then falls back to Claude.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

import httpx

from app.adapters.http import AccessTokenSource
from app.adapters.llm.providers.base import (
    ProviderChunk,
    ProviderError,
    ProviderRefused,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)

type Json = dict[str, object]


def maas_host(region: str) -> str:
    if region == "global":
        return "aiplatform.googleapis.com"
    return f"{region}-aiplatform.googleapis.com"


def maas_url(project: str, region: str) -> str:
    # VERIFY: Model Garden MaaS OpenAI-compatible path for the pinned model and region
    # (M0 spike). Some publishers need a publisher-specific path instead of `endpoints/openapi`.
    return (
        f"https://{maas_host(region)}/v1/projects/{project}/locations/{region}"
        "/endpoints/openapi/chat/completions"
    )


class VertexMaasProvider:
    name = "vertex-maas"

    def __init__(
        self, http: httpx.AsyncClient, tokens: AccessTokenSource, *, project: str, region: str
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._url = maas_url(project, region)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = _body(request, is_streaming=False)
        try:
            response = await self._http.post(self._url, json=body, headers=await self._headers())
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("provider_error") from None
        if not response.is_success:
            raise ProviderError("provider_error")
        return _parse_completion(_json(response.text), request.model)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        body = _body(request, is_streaming=True)
        usage = ProviderUsage()
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=await self._headers()
            ) as response:
                if not response.is_success:
                    raise ProviderError("provider_error")
                async for line in response.aiter_lines():
                    chunk = _parse_stream_line(line)
                    if chunk is None:
                        continue
                    usage = chunk.usage or usage
                    if chunk.text:
                        yield ProviderChunk(text=chunk.text)
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("provider_error") from None
        yield ProviderChunk(usage=usage, model=request.model)

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.token()}"}


def _body(request: ProviderRequest, *, is_streaming: bool) -> Json:
    system = request.system
    if request.json_schema is not None:
        schema = json.dumps(request.json_schema, sort_keys=True)
        system += f"\n\nReply with one JSON object only, matching this JSON Schema:\n{schema}"
    body: Json = {
        "model": request.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": request.user},
        ],
        "max_tokens": request.max_tokens,
        "stream": is_streaming,
    }
    if is_streaming:
        body["stream_options"] = {"include_usage": True}
    return body


def _json(text: str) -> Json:
    try:
        payload: object = json.loads(text)
    except ValueError:
        raise ProviderError("provider_error") from None
    if not isinstance(payload, dict):
        raise ProviderError("provider_error")
    return cast("Json", payload)


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _parse_usage(payload: Json) -> ProviderUsage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    usage = cast("Json", usage)
    return ProviderUsage(
        input_tokens=_int(usage.get("prompt_tokens")),
        output_tokens=_int(usage.get("completion_tokens")),
    )


def _first_choice(payload: Json) -> Json | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = cast("list[object]", choices)[0]
    if not isinstance(first, dict):
        raise ProviderError("provider_error")
    choice = cast("Json", first)
    if choice.get("finish_reason") == "content_filter":
        raise ProviderRefused
    return choice


def _text_of(choice: Json, key: str) -> str | None:
    holder = choice.get(key)
    content = cast("Json", holder).get("content") if isinstance(holder, dict) else None
    return content if isinstance(content, str) else None


def _parse_completion(payload: Json, model: str) -> ProviderResponse:
    choice = _first_choice(payload)
    content = _text_of(choice, "message") if choice is not None else None
    if content is None:
        raise ProviderError("provider_error")
    usage = _parse_usage(payload) or ProviderUsage()
    return ProviderResponse(text=content, model=model, usage=usage)


def _parse_stream_line(line: str) -> ProviderChunk | None:
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None
    payload = _json(data)
    choice = _first_choice(payload)
    text = (_text_of(choice, "delta") or "") if choice is not None else ""
    return ProviderChunk(text=text, usage=_parse_usage(payload))
