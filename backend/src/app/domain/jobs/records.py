"""Value objects for the job-search area. Field names match the table columns and the spec, so
adapters and routes can map them by name."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

type JsonMap = Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class KitAnswerRecord:
    q: str
    a: str


@dataclass(frozen=True, kw_only=True)
class KitRecord:
    role_id: uuid.UUID
    version: int
    generated_at: datetime
    cv: str | None = None
    cv_note: str | None = None
    cover_note: str | None = None
    letter_to: str | None = None
    letter_re: str | None = None
    outreach: str | None = None
    contact: str | None = None
    answers: tuple[KitAnswerRecord, ...] = ()
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True, kw_only=True)
class RoleRecord:
    id: uuid.UUID
    source: str
    title: str
    stage: str
    status: str
    rank: str
    version: int
    found_at: datetime
    created_at: datetime
    updated_at: datetime
    external_id: str | None = None
    url: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None
    pay: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    filter_reason: str | None = None
    track: str | None = None
    mission: str | None = None
    score: int | None = None
    score_breakdown: JsonMap | None = None
    why: str | None = None
    risks: tuple[str, ...] | None = None
    applied_at: datetime | None = None
    followed_up_at: datetime | None = None
    notes: str | None = None
    deleted_at: datetime | None = None
    kit: KitRecord | None = None


@dataclass(frozen=True, kw_only=True)
class RoleEventRecord:
    id: uuid.UUID
    role_id: uuid.UUID
    at: datetime
    kind: str
    from_value: str | None = None
    to_value: str | None = None


@dataclass(frozen=True, kw_only=True)
class ContactRecord:
    id: uuid.UUID
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    url: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    company: str | None = None
    company_norm: str | None = None
    position: str | None = None
    connected_on: date | None = None
    closeness: str | None = None
    message: str | None = None
    notes: str | None = None
    outreach_score: int | None = None
    outreach_reason: str | None = None
    sent_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class TargetRecord:
    id: uuid.UUID
    name: str
    name_key: str
    lane: str
    status: str
    origin: str
    version: int
    created_at: datetime
    updated_at: datetime
    why: str | None = None
    approach: str | None = None
    caveat: str | None = None
    sources: tuple[str, ...] = ()
    notes: str | None = None
    approached_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class ChatRecord:
    id: uuid.UUID
    role_id: uuid.UUID
    author: str
    content: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class RejectionRecord:
    id: uuid.UUID
    role_id: uuid.UUID
    version: int
    stage: str | None = None
    rejected_at: datetime | None = None
    rejection_text: str | None = None
    feedback_text: str | None = None
    channel: str | None = None
    reason_categories: tuple[str, ...] = ()
    summary: str | None = None
    lesson: str | None = None
    use_in_kits: bool = False


@dataclass(frozen=True, kw_only=True)
class FollowupRecord:
    id: uuid.UUID
    role_id: uuid.UUID
    priority: int
    status: str
    version: int
    target_name: str | None = None
    target_role: str | None = None
    source: str | None = None
    contact_id: uuid.UUID | None = None
    channel: str | None = None
    email: str | None = None
    why: str | None = None
    message: str | None = None
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ResearchRunRecord:
    id: uuid.UUID
    target_id: uuid.UUID
    status: str
    created_at: datetime
    focus: tuple[str, ...] = ()
    summary: str | None = None
    sources: tuple[str, ...] = ()
    cost_usd: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class StepRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    kind: str
    summary: str
    at: datetime
    agent: str | None = None
    tool: str | None = None


@dataclass(frozen=True, kw_only=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, kw_only=True)
class ExperienceItem:
    requirement: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CandidateProfile:
    """The signed-in user's own facts (from `user_settings` and their experience records).

    Nothing here is hard-coded: prompts only ever see the caller's data."""

    name: str | None = None
    headline: str | None = None
    location: str | None = None
    # For letter headers only (kit downloads); prompts never see it.
    email: str | None = None
    country: str = "Australia"
    spelling: str = "en-AU"
    skills: str | None = None
    brief: str | None = None
    experience: tuple[ExperienceItem, ...] = ()
    lessons: tuple[str, ...] = ()
    # De-identified text from the uploaded PDFs (technical / leadership CV, LinkedIn profile).
    cv_technical: str | None = None
    cv_leadership: str | None = None
    linkedin_profile: str | None = None
    extra: JsonMap = field(default_factory=dict[str, object])


@dataclass(frozen=True, kw_only=True)
class ActivityRecord:
    """One logged campaign activity (`campaign_activity`)."""

    id: uuid.UUID
    on_date: date
    kind: str
    created_at: datetime
    who: str | None = None
    company: str | None = None
    role_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(frozen=True, kw_only=True)
class RejectionSummaryRecord:
    """A rejection with its role's title, for the Feedback tab."""

    role_id: uuid.UUID
    title: str
    company: str | None = None
    rejected_at: datetime | None = None
    has_text: bool = False
    summary: str | None = None
    categories: tuple[str, ...] = ()
