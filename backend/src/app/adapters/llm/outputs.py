"""Helpers for model answers: JSON validation (ARCH-21), PII re-identification of structured
values, and the Claude refusal-fallback pairing."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from app.adapters.llm.ledger import Attempt
from app.adapters.llm.routing import FeatureRoute
from app.domain.ports import PII

type JsonNode = str | int | float | bool | list[JsonNode] | dict[str, JsonNode] | None


def refusal_fallback(attempt: Attempt, route: FeatureRoute) -> str | None:
    """Claude only: the model the client-side refusal middleware retries on."""
    fallback = route.fallback
    is_claude_pair = (
        not attempt.is_fallback
        and attempt.choice.provider == "vertex-claude"
        and fallback is not None
        and fallback.provider == "vertex-claude"
    )
    return fallback.model if is_claude_pair and fallback else None


async def reidentify_node(pii: PII, node: JsonNode) -> JsonNode:
    if isinstance(node, str):
        return await pii.reidentify(node)
    if isinstance(node, list):
        return [await reidentify_node(pii, item) for item in node]
    if isinstance(node, dict):
        return {key: await reidentify_node(pii, item) for key, item in node.items()}
    return node


def validate_json[T: BaseModel](text: str, output: type[T]) -> T | None:
    candidate = _strip_fences(text)
    try:
        return output.model_validate_json(candidate)
    except ValidationError:
        return None


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.removesuffix("```").strip()
    try:
        json.loads(stripped)
    except ValueError:
        return text
    return stripped
