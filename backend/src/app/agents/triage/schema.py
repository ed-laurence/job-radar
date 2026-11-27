"""Triage's structured output. Validated by the LLM adapter: one retry, then the fallback
model, then `ValidationFailed` (ARCH-21)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Executable criteria name a test node: a test file under web/tests or backend/tests, then
# `::` and the test name. Prose ("it should work") is refused.
TEST_ID_PATTERN = r"^(web/tests/|backend/tests/)[A-Za-z0-9_./-]+::[^\n]{1,200}$"


class CriterionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(max_length=300, pattern=TEST_ID_PATTERN)
    expectation: str = Field(min_length=1, max_length=1000)


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["feature", "bug", "chore", "refactor", "test", "docs"]
    size: Literal["S", "M", "L"]
    risk: Literal["low", "medium", "high"]
    areas: list[Literal["web", "api"]] = Field(max_length=2)
    acceptance_criteria: list[CriterionOut] = Field(min_length=1, max_length=30)
    needs_human_design: bool
    out_of_scope: bool
    out_of_scope_reason: str | None = Field(default=None, max_length=1000)
    likely_paths: list[str] = Field(default_factory=list[str], max_length=30)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(set(self.areas)) != len(self.areas):
            raise ValueError("areas must not repeat")
        if self.out_of_scope and not (self.out_of_scope_reason or "").strip():
            raise ValueError("out_of_scope needs a reason")
        if not self.out_of_scope and not self.areas:
            raise ValueError("an in-scope task names at least one area")
        if any(len(path) > 300 for path in self.likely_paths):
            raise ValueError("paths are at most 300 characters")
        return self
