"""Read the sandboxed reviewer's `/out/review.json` into a `ReviewDraft`.

The file is untrusted output of agent-controlled code: it is size-capped, strictly typed
(unknown keys refused, no type coercion, every key present) and must name the run's own task.
Anything else is `ReviewFileError`, whose fixed message is safe to show. Whether a BLOCKER's
citation holds, duplicates and the verdict are the domain's call
(`app.domain.devboard.review_policy`), not this parser's.

Contract (`sandbox/writer/review_contract.py`): {"task_id", "summary" (<= 80 words),
"findings": [{"severity": BLOCKER|NIT, "rubric_id", "test_id", "repro" (<= 60 words), "path",
"line", "evidence" (<= 60 words), "fix" (<= 60 words)}] (<= 10)}.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.domain.devboard.model import ReviewFinding, ReviewSeverity
from app.domain.ports.devboard import ReviewDraft

MAX_REVIEW_BYTES = 32 * 1024
MAX_FINDINGS = 10
SUMMARY_WORDS = 80
FINDING_WORDS = 60
MALFORMED = "The reviewer's answer is malformed."
_RUBRIC_ID = r"^[A-Z]{2,10}-[0-9]{1,3}$"
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/@+-]+$")


class ReviewFileError(Exception):
    """The review file is missing or malformed. The message is safe to show."""


def _words(limit: int) -> AfterValidator:
    def check(text: str) -> str:
        if not text.strip():
            raise ValueError("must not be blank")
        if len(text.split()) > limit:
            raise ValueError(f"at most {limit} words")
        return text

    return AfterValidator(check)


def _repo_path(path: str) -> str:
    parts = path.split("/")
    if (
        path.startswith("/")
        or not _SAFE_PATH.match(path)
        or any(p in {"", ".", ".."} for p in parts)
    ):
        raise ValueError("must be a repo-relative file path")
    return path


type _Summary = Annotated[str, Field(max_length=800), _words(SUMMARY_WORDS)]
type _Text = Annotated[str, Field(max_length=600), _words(FINDING_WORDS)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ReviewFileFinding(_Strict):
    severity: Literal["BLOCKER", "NIT"]
    rubric_id: str | None = Field(pattern=_RUBRIC_ID)
    test_id: str | None = Field(min_length=1, max_length=300)
    repro: _Text | None
    path: Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_repo_path)]
    line: int | None = Field(ge=1, le=1_000_000)
    evidence: _Text
    fix: _Text | None


class ReviewFile(_Strict):
    task_id: str = Field(max_length=64)
    summary: _Summary
    findings: list[ReviewFileFinding] = Field(max_length=MAX_FINDINGS)


def _finding(found: ReviewFileFinding) -> ReviewFinding:
    return ReviewFinding(
        severity=ReviewSeverity(found.severity),
        path=found.path,
        evidence=found.evidence,
        rubric_id=found.rubric_id,
        test_id=found.test_id,
        repro=found.repro,
        line=found.line,
        fix=found.fix,
    )


def parse_review(raw: bytes, task_id: str) -> ReviewDraft:
    if len(raw) > MAX_REVIEW_BYTES:
        raise ReviewFileError("The reviewer's answer is too large.")
    try:
        review = ReviewFile.model_validate_json(raw)
    except ValidationError:
        raise ReviewFileError(MALFORMED) from None
    if review.task_id != task_id:
        raise ReviewFileError("The reviewer's answer names another task.")
    return ReviewDraft(
        summary=review.summary, findings=tuple(_finding(item) for item in review.findings)
    )


def read_review(path: Path, task_id: str) -> ReviewDraft:
    """Blocking (run it in a thread)."""
    if path.is_symlink():
        # The agent controls /out; a link could point anywhere on the host.
        raise ReviewFileError("The reviewer's answer is not a regular file.")
    try:
        if not path.is_file():
            raise ReviewFileError("The reviewer wrote no review.")
        if path.stat().st_size > MAX_REVIEW_BYTES:
            raise ReviewFileError("The reviewer's answer is too large.")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ReviewFileError("The reviewer wrote no review.") from None
    return parse_review(raw, task_id)
