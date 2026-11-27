"""Rejection analysis (ported from job-seeker's `rejections.analyse`), on the open-model route
(`rejection_analysis` in `ai/models.yaml`, with the Claude fallback handled by the LLM adapter).

Reasons are sorted into the fixed categories the dashboard counts; `patterns` summarises them
across every recorded rejection.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.ports import RejectionStore
from app.domain.jobs.records import RejectionRecord
from app.domain.jobs.roles import RoleService
from app.domain.ports import LLM, Clock, DataBlock, LlmAttribution, LlmFeature, LlmRequest

# Patterns look at this many recent rejections and show at most this many lessons.
PATTERN_WINDOW = 500
MAX_PATTERN_CATEGORIES = 20
MAX_PATTERN_LESSONS = 5

Category = Literal[
    "skills_gap",
    "experience_level",
    "seniority",
    "location",
    "pay",
    "visa",
    "internal_candidate",
    "role_filled",
    "stronger_candidate",
    "culture_fit",
    "no_reason_given",
    "other",
]

ANALYSE_SYSTEM = (
    "You read a rejection a job seeker received and record why they were turned down, so "
    "patterns across applications can be counted."
)
ANALYSE_INSTRUCTIONS = """Record the reasons in the rejection message and any feedback.
- reasons: each distinct reason actually stated or clearly implied, with the closest category and
  a short detail in their terms. Don't speculate beyond the text. If neither message gives a
  reason, return a single "no_reason_given". Generic phrases like "other candidates more closely
  matched" are "stronger_candidate" with detail "generic; no specifics".
- summary: one sentence on what they said.
- lesson: one or two sentences of practical guidance for future applications to similar roles,
  based only on what they said. Never suggest claiming experience the candidate doesn't have. If
  there's nothing to learn, say so plainly."""


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    detail: str = Field(max_length=300)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasons: list[Reason] = Field(min_length=1, max_length=12)
    summary: str = Field(max_length=500)
    lesson: str = Field(max_length=1000)


class RejectionService:
    def __init__(
        self, role_service: RoleService, rejections: RejectionStore, llm: LLM, clock: Clock
    ) -> None:
        self._role_service = role_service
        self._rejections = rejections
        self._llm = llm
        self._clock = clock

    async def analyse(self, principal: Principal, role_id: uuid.UUID) -> RejectionRecord:
        role = await self._role_service.get_role(principal, role_id)
        rejection = await self._rejections.get_rejection(principal.user_id, role_id)
        if rejection is None:
            raise NotFound("That role has no rejection recorded.")
        if not (rejection.rejection_text or rejection.feedback_text):
            raise ValidationFailed("Paste their rejection message or their feedback first.")
        blocks = [DataBlock("role", f"{role.title} at {role.company or 'unknown company'}")]
        if rejection.rejection_text:
            blocks.append(DataBlock("rejection_message", rejection.rejection_text))
        if rejection.feedback_text:
            blocks.append(DataBlock("feedback", rejection.feedback_text))
        request = LlmRequest(
            feature=LlmFeature.REJECTION_ANALYSIS,
            system=ANALYSE_SYSTEM,
            instructions=ANALYSE_INSTRUCTIONS,
            attribution=LlmAttribution(user_id=principal.user_id, role_id=role_id),
            data=tuple(blocks),
        )
        analysis = (await self._llm.generate(request, Analysis)).value
        source = "feedback" if rejection.feedback_text else "rejection"
        reasons = [
            {"category": r.category, "detail": r.detail.strip(), "source": source}
            for r in analysis.reasons
        ]
        categories = list(dict.fromkeys(reason.category for reason in analysis.reasons))
        now = self._clock.now()
        values: dict[str, object] = {
            "reasons": reasons,
            "reason_categories": categories,
            "summary": analysis.summary.strip(),
            "lesson": analysis.lesson.strip(),
            "analysed_at": now,
            "updated_at": now,
        }
        return await self._rejections.update_rejection(principal.user_id, role_id, values)

    async def patterns(self, principal: Principal) -> RejectionPatterns:
        rows = await self._rejections.list_rejections(principal.user_id, PATTERN_WINDOW)
        return rejection_patterns(rows)


@dataclass(frozen=True)
class RejectionPatterns:
    total: int
    analysed: int
    categories: tuple[tuple[str, int], ...]
    lessons: tuple[str, ...]


def rejection_patterns(rows: Sequence[RejectionRecord]) -> RejectionPatterns:
    """Category counts (most common first, then by name), and the latest distinct lessons."""
    counts = Counter(category for row in rows for category in row.reason_categories)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    lessons = dict.fromkeys(row.lesson.strip() for row in rows if row.lesson and row.lesson.strip())
    return RejectionPatterns(
        total=len(rows),
        analysed=sum(1 for row in rows if row.summary is not None),
        categories=tuple(ordered[:MAX_PATTERN_CATEGORIES]),
        lessons=tuple(lessons)[:MAX_PATTERN_LESSONS],
    )
