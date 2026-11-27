"""Replay stays strict by default; dev-only `synthesize` answers unrecorded prompts with
schema-valid output; area fixture folders (`<root>/<area>/<feature>/`) are found."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from app.adapters.llm.providers.base import ProviderRequest
from app.adapters.llm.providers.replay import ReplayFixtureMissing, ReplayProvider
from app.adapters.llm.providers.synthetic import SYNTHETIC_MODEL, SyntheticProvider
from app.agents.triage.schema import TriageOutput
from app.domain.devboard.paths import normalise

HASH = "0123456789abcdef0123456789abcdef"


class Size(StrEnum):
    S = "S"
    M = "M"


class Criterion(BaseModel):
    text: str = Field(min_length=12, max_length=80)
    weight: int = Field(ge=1, le=5)


class Triage(BaseModel):
    size: Size
    out_of_scope: bool
    reason: str | None = None
    criteria: list[Criterion] = Field(min_length=2, max_length=4)


def _request(schema: dict[str, object] | None = None, prompt_hash: str = HASH) -> ProviderRequest:
    return ProviderRequest(
        feature="triage",
        prompt_hash=prompt_hash,
        model="claude-opus-5-5",
        effort="low",
        system="s",
        user="u",
        max_tokens=256,
        json_schema=schema,
    )


async def test_unrecorded_prompt_fails_loudly_by_default(tmp_path: Path) -> None:
    with pytest.raises(ReplayFixtureMissing):
        await ReplayProvider(tmp_path).complete(_request())


async def test_unrecorded_prompt_is_synthesized_when_enabled(tmp_path: Path) -> None:
    provider = ReplayProvider(tmp_path, on_miss=SyntheticProvider())
    response = await provider.complete(_request(Triage.model_json_schema()))

    assert response.model == SYNTHETIC_MODEL
    parsed = Triage.model_validate_json(response.text)  # schema-valid, incl. bounds and enums
    assert len(parsed.criteria) >= 2


async def test_synthetic_satisfies_the_real_triage_schema() -> None:
    schema = TriageOutput.model_json_schema()
    response = await SyntheticProvider().complete(_request(schema))
    parsed = TriageOutput.model_validate_json(response.text)  # incl. the test_id pattern

    assert parsed.acceptance_criteria


# Regression (e2e): synthetic `likely_paths` were sentences, so the domain marked every offline
# triage out of scope ("Names paths that aren't concrete repo paths").
async def test_synthetic_triage_survives_domain_path_rules() -> None:
    response = await SyntheticProvider().complete(_request(TriageOutput.model_json_schema()))
    parsed = TriageOutput.model_validate_json(response.text)

    assert parsed.likely_paths
    assert all(normalise(path) == path for path in parsed.likely_paths)
    assert not parsed.out_of_scope


async def test_synthetic_output_is_deterministic_per_prompt() -> None:
    schema = Triage.model_json_schema()
    provider = SyntheticProvider()
    first = (await provider.complete(_request(schema))).text
    again = (await provider.complete(_request(schema))).text
    other = (await provider.complete(_request(schema, prompt_hash="f" * 32))).text

    assert first == again
    assert first != other


async def test_synthetic_stream_is_labelled_as_synthetic() -> None:
    chunks = [chunk.text async for chunk in SyntheticProvider().stream(_request()) if chunk.text]
    assert "".join(chunks).startswith("[synthetic triage output")


async def test_area_fixture_folder_is_found(tmp_path: Path) -> None:
    folder = tmp_path / "devboard" / "triage"
    folder.mkdir(parents=True)
    fixture = {"feature": "triage", "prompt_hash": HASH, "model": "recorded", "text": "hello"}
    (folder / f"{HASH}.json").write_text(json.dumps(fixture), encoding="utf-8")

    response = await ReplayProvider(tmp_path).complete(_request())

    assert (response.text, response.model) == ("hello", "recorded")
