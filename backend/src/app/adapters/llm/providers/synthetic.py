"""Deterministic stand-in LLM for local dev with new content (`LLM_REPLAY_MISS=synthesize`).

Replay only knows recorded prompts, so a role or task created locally has no fixture. In dev,
`ReplayProvider` can hand such misses to this provider instead of failing. Output is derived from
the prompt hash (same prompt, same answer) and, for structured features, built to satisfy the
request's JSON schema. Tool-use runs get a two-turn script: the first turn calls the first tool
on offer with arguments built from its schema, the next turn gives the final answer.
Tests never use it as a fallback: the default stays a loud `ReplayFixtureMissing`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import cast

from app.adapters.llm.providers.base import (
    ProviderCall,
    ProviderChunk,
    ProviderRequest,
    ProviderResponse,
    ProviderToolRequest,
    ProviderTurn,
    ProviderUsage,
)
from app.domain.ports import ToolOutput

SYNTHETIC_MODEL = "synthetic-local"
_CHUNK_CHARS = 24
_MAX_DEPTH = 8
_WORDS = (
    "clear", "practical", "measured", "evidence", "delivery", "platform", "team", "outcome",
    "experience", "impact", "design", "quality", "reliable", "users", "data", "insight",
)  # fmt: skip

JsonSchema = Mapping[str, object]
_SYNTHETIC_PATH = "web/src/features/synthetic/"
_PATTERN_CANDIDATES = (
    "backend/tests/unit/test_synthetic.py::test_synthetic_behaviour",
    "web/tests/e2e/synthetic.spec.ts::renders the synthetic state",
    "SyntheticLocal0000000",
    "/synthetic",
    "/",
    "1",
)


class _Picker:
    """Deterministic word choices from a seed (not randomness: same seed, same sequence)."""

    def __init__(self, seed: str) -> None:
        self._seed = seed
        self._counter = 0

    def word(self) -> str:
        self._counter += 1
        digest = hashlib.sha256(f"{self._seed}:{self._counter}".encode()).digest()
        return _WORDS[digest[0] % len(_WORDS)]

    def sentence(self, words: int = 10) -> str:
        return " ".join(self.word() for _ in range(words)).capitalize() + "."


def _as_map(value: object) -> JsonSchema:
    return cast("JsonSchema", value) if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    return list(cast("list[object]", value)) if isinstance(value, list) else []


def synthetic_text(request: ProviderRequest) -> str:
    picker = _Picker(request.prompt_hash)
    paragraphs = [" ".join(picker.sentence() for _ in range(3)) for _ in range(3)]
    header = f"[synthetic {request.feature} output: offline dev, not a real model response]"
    return "\n\n".join([header, *paragraphs])


def _resolve(schema: JsonSchema, root: JsonSchema) -> JsonSchema:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    node: JsonSchema = root
    for part in ref[2:].split("/"):
        node = _as_map(node.get(part))
    return node


def _int_bound(schema: JsonSchema, key: str, default: int) -> int:
    value = schema.get(key)
    return int(value) if isinstance(value, int | float) else default


def _choice(schema: JsonSchema) -> JsonSchema | None:
    options = _as_list(schema.get("anyOf")) or _as_list(schema.get("oneOf"))
    if not options:
        return None
    maps = [_as_map(option) for option in options]
    non_null = [option for option in maps if option.get("type") != "null"]
    return non_null[0] if non_null else maps[0]


def _kind(schema: JsonSchema) -> str:
    kind = schema.get("type")
    if isinstance(kind, str):
        return kind
    kinds = [k for k in _as_list(kind) if isinstance(k, str) and k != "null"]
    if kinds:
        return kinds[0]
    return "object" if "properties" in schema else "string"


class _Synthesizer:
    def __init__(self, root: JsonSchema, picker: _Picker) -> None:
        self._root = root
        self._picker = picker
        self._by_kind: Mapping[str, Callable[[JsonSchema, int, str], object]] = {
            "object": self._object,
            "array": self._array,
            "integer": lambda schema, _depth, _hint: _int_bound(schema, "minimum", 0),
            "number": lambda schema, _depth, _hint: float(_int_bound(schema, "minimum", 0)),
            "boolean": lambda _schema, _depth, _hint: False,
            "null": lambda _schema, _depth, _hint: None,
        }

    def value(self, schema: JsonSchema, depth: int = 0, hint: str = "") -> object:
        """`hint` is the enclosing property name, so semantically constrained but unpatterned
        fields (e.g. `likely_paths`) get a plausible shape that domain validation accepts."""
        schema = _resolve(schema, self._root)
        choice = _choice(schema)
        if choice is not None:
            return self.value(choice, depth, hint)
        if "const" in schema:
            return schema["const"]
        enum = _as_list(schema.get("enum"))
        if enum:
            return enum[0]
        if depth > _MAX_DEPTH:
            return None
        build = self._by_kind.get(_kind(schema), self._string)
        return build(schema, depth, hint)

    def _object(self, schema: JsonSchema, depth: int, _hint: str) -> object:
        properties = _as_map(schema.get("properties"))
        return {
            name: self.value(_as_map(sub), depth + 1, hint=name) for name, sub in properties.items()
        }

    def _array(self, schema: JsonSchema, depth: int, hint: str) -> object:
        count = max(_int_bound(schema, "minItems", 1), 1)
        count = min(count, _int_bound(schema, "maxItems", count))
        items = _as_map(schema.get("items"))
        return [self.value(items, depth + 1, hint) for _ in range(count)]

    def _string(self, schema: JsonSchema, _depth: int, hint: str) -> object:
        low = _int_bound(schema, "minLength", 1)
        high = max(_int_bound(schema, "maxLength", 120), low)
        if "path" in hint.lower() and "pattern" not in schema:
            return _SYNTHETIC_PATH  # a concrete, in-scope repo path (dev-board write allowlist)
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            # Patterned fields: first candidate shape the pattern accepts (covers ids, slugs,
            # ranks, route templates and test ids). No match falls through to plain text,
            # which then fails validation and is handled like any unusable model answer.
            match = next((c for c in _PATTERN_CANDIDATES if re.search(pattern, c)), None)
            if match is not None:
                return match
        text = self._picker.sentence(words=6)
        while len(text) < low:
            text = f"{text} {self._picker.sentence(words=6)}"
        return text[:high]


def synthesize(schema: JsonSchema, seed: str) -> object:
    return _Synthesizer(schema, _Picker(seed)).value(schema)


class SyntheticProvider:
    name = "synthetic"

    def _text(self, request: ProviderRequest) -> str:
        if request.json_schema is None:
            return synthetic_text(request)
        return json.dumps(synthesize(request.json_schema, request.prompt_hash))

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        text = self._text(request)
        return ProviderResponse(text=text, model=SYNTHETIC_MODEL, usage=ProviderUsage())

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderChunk]:
        text = self._text(request)
        for start in range(0, len(text), _CHUNK_CHARS):
            yield ProviderChunk(text=text[start : start + _CHUNK_CHARS])
        yield ProviderChunk(usage=ProviderUsage(), model=SYNTHETIC_MODEL)

    async def next_turn(self, request: ProviderToolRequest) -> ProviderTurn:
        has_output = any(isinstance(item, ToolOutput) for item in request.transcript)
        if request.tools and not has_output:
            tool = request.tools[0]
            arguments = synthesize(tool.input_schema, request.prompt_hash)
            call = ProviderCall(
                id=f"synthetic-{request.prompt_hash[:12]}",
                name=tool.name,
                arguments=cast("dict[str, object]", arguments)
                if isinstance(arguments, dict)
                else {},
            )
            text = f"[synthetic {request.feature} step] Calling {tool.name}."
            return ProviderTurn(text=text, model=SYNTHETIC_MODEL, call=call)
        answer = json.dumps(synthesize(request.answer_schema, request.prompt_hash))
        return ProviderTurn(text=answer, model=SYNTHETIC_MODEL)
