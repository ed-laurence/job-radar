"""Agent coordination messages with server-side caps (PLAN "Keeping agent communication
concise"). An oversized message is **refused**, never cut: these validators only reject.

Caps: brief <= 800 tokens, handoff <= 400, request_help <= 200, findings <= 10 per reviewer
and <= 60 words each. Tokens are estimated without a tokenizer as the larger of
characters / 4 and words * 4 / 3 over the message's JSON, which over-counts rather than
under-counts for English and code-like text.
"""

from __future__ import annotations

import math
import re
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.errors import FieldError, ValidationFailed

BRIEF_TOKENS = 800
HANDOFF_TOKENS = 400
REQUEST_HELP_TOKENS = 200
MAX_FINDINGS = 10
FINDING_WORDS = 60
SUMMARY_WORDS = 80
MAX_OPEN_QUESTIONS = 3

# Handoffs carry references, not content: no code fences, diff hunks or tracebacks.
_CONTENT = re.compile(r"```|^(\+\+\+|---|@@) |^Traceback \(most recent call last\)", re.M)
_RUBRIC = r"^[A-Z]{2,5}-\d{2}$"


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return max(math.ceil(len(text) / 4), math.ceil(words * 4 / 3))


def word_count(text: str) -> int:
    return len(text.split())


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # A class constant, not a field: a sender can't raise its own cap.
    token_cap: ClassVar[int] = 0

    @model_validator(mode="after")
    def _within_cap(self) -> Self:
        cap = type(self).token_cap
        used = estimate_tokens(self.model_dump_json())
        if used > cap:
            raise ValueError(f"message is about {used} tokens; the cap is {cap}. Shorten it.")
        return self


class Brief(_Message):
    token_cap: ClassVar[int] = BRIEF_TOKENS
    task_id: str = Field(max_length=64)
    subtask_id: str = Field(max_length=64)
    agent: str = Field(max_length=64)
    goal: str = Field(min_length=1)
    lease_paths: list[str] = Field(max_length=50)
    doc_links: list[str] = Field(default_factory=list[str], max_length=20)
    acceptance_tests: list[str] = Field(default_factory=list[str], max_length=50)


class TestCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    first_failing: str | None = Field(default=None, max_length=300)


class Handoff(_Message):
    token_cap: ClassVar[int] = HANDOFF_TOKENS
    status: Literal["done", "partial", "blocked", "failed"]
    summary: str = Field(min_length=1)
    bundle_ref: str | None = Field(default=None, max_length=300)
    changed_paths: list[str] = Field(default_factory=list[str], max_length=100)
    tests: TestCounts
    rubric_checked: list[str] = Field(default_factory=list[str], max_length=40)
    open_questions: list[str] = Field(default_factory=list[str])
    artifacts: list[str] = Field(default_factory=list[str], max_length=20)

    @model_validator(mode="after")
    def _references_only(self) -> Self:
        if word_count(self.summary) > SUMMARY_WORDS:
            raise ValueError(f"summary is over {SUMMARY_WORDS} words")
        if len(self.open_questions) > MAX_OPEN_QUESTIONS:
            raise ValueError(f"at most {MAX_OPEN_QUESTIONS} open questions")
        if any(not re.match(_RUBRIC, rubric) for rubric in self.rubric_checked):
            raise ValueError("rubric_checked holds rubric IDs like ARCH-01")
        texts = [self.summary, *self.open_questions]
        if any(_CONTENT.search(text) for text in texts):
            raise ValueError("handoffs reference artifacts; no code, diffs or logs")
        return self


class RequestHelp(_Message):
    token_cap: ClassVar[int] = REQUEST_HELP_TOKENS
    run_id: str = Field(max_length=64)
    question: str = Field(min_length=1)
    blocking: bool = True


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rubric_id: str = Field(pattern=_RUBRIC)
    severity: Literal["BLOCKER", "MAJOR", "MINOR", "NIT"]
    path: str = Field(max_length=300)
    line: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _short(self) -> Self:
        if word_count(self.text) > FINDING_WORDS:
            raise ValueError(f"a finding is at most {FINDING_WORDS} words")
        return self


class Findings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: str = Field(max_length=64)
    findings: list[Finding]

    @model_validator(mode="after")
    def _few(self) -> Self:
        if len(self.findings) > MAX_FINDINGS:
            raise ValueError(f"at most {MAX_FINDINGS} findings per reviewer")
        return self


def parse_message[T: BaseModel](model: type[T], raw: str | bytes) -> T:
    """Validate a message from an agent, or refuse it with `ValidationFailed`."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        errors = [
            FieldError(
                pointer="/" + "/".join(str(part) for part in item["loc"]),
                message=str(item["msg"])[:300],
            )
            for item in error.errors(include_input=False)
        ]
        raise ValidationFailed(f"{model.__name__} refused.", errors=errors) from None
