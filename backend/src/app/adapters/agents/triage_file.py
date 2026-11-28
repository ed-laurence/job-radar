"""Read the sandboxed triager's `/out/triage.json` into a `TriageDecision`.

The file is untrusted output of agent-controlled code: it is size-capped, strictly typed
(unknown keys refused, no type coercion), internally consistent and must name the run's own
task. Anything else is `TriageFileError`, whose fixed message is safe to show. Whether the
paths exist and the duplicate is an open card is the domain's call
(`app.domain.devboard.triage_policy`), not this parser's.

Contract (`sandbox/writer/triage_contract.py`): {"task_id", "type", "size", "risk", "areas",
"priority", "severity", "impact", "route", "route_reason", "needs_human_design",
"out_of_scope", "out_of_scope_reason", "likely_paths", "verification": {"status",
"evidence", "code_refs", "duplicate_of"}}. There are no acceptance criteria: the planner
writes them.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.domain.devboard.model import (
    Area,
    Priority,
    Risk,
    Route,
    Severity,
    Size,
    TaskType,
    Verification,
    VerificationStatus,
)
from app.domain.ports.devboard import TriageDecision

MAX_TRIAGE_BYTES = 32 * 1024
MALFORMED = "The triager's answer is malformed."

type _Path = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class TriageFileError(Exception):
    """The triage file is missing or malformed. The message is safe to show."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TriageFileVerification(_Strict):
    status: Literal["confirmed", "not_reproduced", "unclear", "duplicate"]
    evidence: str = Field(min_length=1, max_length=1000)
    code_refs: list[_Path] = Field(max_length=20)
    duplicate_of: str | None = Field(max_length=36)

    @model_validator(mode="after")
    def _duplicate_names_a_card(self) -> Self:
        if (self.status == "duplicate") != (self.duplicate_of is not None):
            raise ValueError("duplicate_of is set exactly for a duplicate")
        if self.duplicate_of is not None:
            uuid.UUID(self.duplicate_of)  # ValueError -> ValidationError
        return self


class TriageFile(_Strict):
    task_id: str = Field(max_length=64)
    type: Literal["feature", "bug", "security", "performance", "chore", "refactor", "test", "docs"]
    size: Literal["S", "M", "L"]
    risk: Literal["low", "medium", "high"]
    areas: list[Literal["web", "api"]] = Field(max_length=2)
    priority: Literal["P0", "P1", "P2", "P3"]
    severity: Literal["critical", "major", "minor", "trivial"]
    impact: str = Field(min_length=1, max_length=600)
    route: Literal["now", "next", "later"]
    route_reason: str = Field(min_length=1, max_length=400)
    needs_human_design: bool
    out_of_scope: bool
    out_of_scope_reason: str | None = Field(max_length=1000)
    likely_paths: list[_Path] = Field(max_length=30)
    verification: TriageFileVerification

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(set(self.areas)) != len(self.areas):
            raise ValueError("areas must not repeat")
        if not self.out_of_scope and not self.areas:
            raise ValueError("an in-scope card names at least one area")
        has_reason = bool((self.out_of_scope_reason or "").strip())
        if self.out_of_scope != has_reason or (self.out_of_scope_reason is not None) != has_reason:
            raise ValueError("out_of_scope_reason is given exactly for an out-of-scope card")
        return self


def _decision(triage: TriageFile) -> TriageDecision:
    found = triage.verification
    duplicate = None if found.duplicate_of is None else uuid.UUID(found.duplicate_of)
    return TriageDecision(
        type=TaskType(triage.type),
        size=Size(triage.size),
        llm_risk=Risk(triage.risk),
        areas=tuple(Area(area) for area in triage.areas),
        priority=Priority(triage.priority),
        severity=Severity(triage.severity),
        impact=triage.impact,
        route=Route(triage.route),
        route_reason=triage.route_reason,
        verification=Verification(
            status=VerificationStatus(found.status),
            evidence=found.evidence,
            code_refs=tuple(found.code_refs),
            duplicate_of=duplicate,
        ),
        needs_human_design=triage.needs_human_design,
        likely_paths=tuple(triage.likely_paths),
        out_of_scope_reason=triage.out_of_scope_reason if triage.out_of_scope else None,
    )


def parse_triage(raw: bytes, task_id: str) -> TriageDecision:
    if len(raw) > MAX_TRIAGE_BYTES:
        raise TriageFileError("The triager's answer is too large.")
    try:
        triage = TriageFile.model_validate_json(raw)
    except ValidationError:
        raise TriageFileError(MALFORMED) from None
    if triage.task_id != task_id:
        raise TriageFileError("The triager's answer names another task.")
    return _decision(triage)


def read_triage(path: Path, task_id: str) -> TriageDecision:
    """Blocking (run it in a thread)."""
    if path.is_symlink():
        # The agent controls /out; a link could point anywhere on the host.
        raise TriageFileError("The triager's answer is not a regular file.")
    try:
        if not path.is_file():
            raise TriageFileError("The triager wrote no triage.")
        if path.stat().st_size > MAX_TRIAGE_BYTES:
            raise TriageFileError("The triager's answer is too large.")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise TriageFileError("The triager wrote no triage.") from None
    return parse_triage(raw, task_id)
