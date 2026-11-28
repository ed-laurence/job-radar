"""Triage's structured output. Validated by the LLM adapter: one retry, then the fallback
model, then `ValidationFailed` (ARCH-21).

Triage verifies, rates and routes a card; it writes no acceptance criteria (the planner does).
Open cards are shown to the model as short refs (`C1`, `C2`, ...), so `duplicate_of` is a ref
and the agent maps it back to the card id.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CARD_REF_PATTERN = r"^C[1-9][0-9]{0,2}$"
MAX_PATH_CHARS = 300


class VerificationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "not_reproduced", "unclear", "duplicate"]
    evidence: str = Field(min_length=1, max_length=1000)
    code_refs: list[str] = Field(default_factory=list[str], max_length=20)
    duplicate_of: str | None = Field(default=None, pattern=CARD_REF_PATTERN)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status == "duplicate") != (self.duplicate_of is not None):
            raise ValueError("duplicate_of is set exactly when status is duplicate")
        if any(len(path) > MAX_PATH_CHARS for path in self.code_refs):
            raise ValueError("paths are at most 300 characters")
        return self


class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["feature", "bug", "security", "performance", "chore", "refactor", "test", "docs"]
    size: Literal["S", "M", "L"]
    risk: Literal["low", "medium", "high"]
    areas: list[Literal["web", "api"]] = Field(max_length=2)
    priority: Literal["P0", "P1", "P2", "P3"]
    severity: Literal["critical", "major", "minor", "trivial"]
    impact: str = Field(min_length=1, max_length=600)
    route: Literal["now", "next", "later"]
    route_reason: str = Field(min_length=1, max_length=400)
    verification: VerificationOut
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
        if any(len(path) > MAX_PATH_CHARS for path in self.likely_paths):
            raise ValueError("paths are at most 300 characters")
        return self
