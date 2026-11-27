"""Read the sandbox's `/out/handoff.json` (shape written by `sandbox/writer/handoff.py`).

The file is untrusted output of agent-controlled code: it is size-capped, strictly typed and
must name the run's own task. Anything else is `HandoffError`, which fails the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_HANDOFF_BYTES = 64 * 1024
MAX_ITEM = 200


class HandoffError(Exception):
    """The handoff is missing or malformed. The message is safe to show."""


class HandoffTests(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(default=0, ge=0)
    first_failing: str | None = Field(default=None, max_length=MAX_ITEM)


class Handoff(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    task_id: str = Field(max_length=64)
    status: Literal["done", "failed", "error"]
    summary: str = Field(max_length=2000)
    changed_paths: tuple[str, ...] = Field(default=(), max_length=1000)
    tests: HandoffTests
    rubric_checked: tuple[str, ...] = Field(default=(), max_length=50)
    open_questions: tuple[str, ...] = Field(default=(), max_length=3)
    skipped_checks: tuple[str, ...] = Field(default=(), max_length=10)


def parse_handoff(raw: bytes, task_id: str) -> Handoff:
    if len(raw) > MAX_HANDOFF_BYTES:
        raise HandoffError("The sandbox handoff is too large.")
    try:
        handoff = Handoff.model_validate_json(raw)
    except ValidationError:
        raise HandoffError("The sandbox handoff is malformed.") from None
    if handoff.task_id != task_id:
        raise HandoffError("The sandbox handoff names another task.")
    return handoff


def read_handoff(path: Path, task_id: str) -> Handoff:
    """Blocking (run it in a thread)."""
    if path.is_symlink():
        # The agent controls /out; a link could point anywhere on the host.
        raise HandoffError("The sandbox handoff is not a regular file.")
    try:
        size = path.stat().st_size
        if size > MAX_HANDOFF_BYTES:
            raise HandoffError("The sandbox handoff is too large.")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise HandoffError("The sandbox wrote no handoff.") from None
    return parse_handoff(raw, task_id)
