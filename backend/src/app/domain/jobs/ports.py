"""Ports for the job-search area (ARCH-02). Postgres adapters live in
`app.adapters.db.jobs_*`; in-memory fakes in `app.adapters.fakes.jobs`.

Every method takes the caller's `user_id`, which the adapter uses only to scope row-level
security (`app.user_id`); it never comes from a request body (ARCH-05).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.domain.jobs.records import (
    ActivityRecord,
    CandidateProfile,
    ChatRecord,
    ContactRecord,
    FollowupRecord,
    KitRecord,
    RejectionRecord,
    RejectionSummaryRecord,
    ResearchRunRecord,
    RoleEventRecord,
    RoleRecord,
    StepRecord,
    TargetRecord,
)

type Values = Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class RoleQuery:
    statuses: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    track: str | None = None
    min_score: int | None = None
    text: str | None = None
    sort: str = "rank"
    min_age_days: int | None = None
    max_age_days: int | None = None
    # Resolved by `RoleService` from the age filters and the Clock. A role's age runs from
    # `posted_at`, falling back to `found_at`: kept when `seen_from <= that < seen_before`.
    seen_from: datetime | None = None
    seen_before: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class NewRoleEvent:
    id: uuid.UUID
    at: datetime
    kind: str
    from_value: str | None = None
    to_value: str | None = None


@dataclass(frozen=True, kw_only=True)
class RoleWrite:
    """One atomic change to a role: column values, events, and an optional rejection record."""

    role_id: uuid.UUID
    version: int | None
    values: Values
    events: tuple[NewRoleEvent, ...] = ()
    rejection: Values | None = None


class RoleStore(Protocol):
    async def list_roles(
        self, user_id: uuid.UUID, query: RoleQuery, offset: int, limit: int
    ) -> list[RoleRecord]: ...

    async def get_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> RoleRecord | None:
        """Excludes soft-deleted rows. Includes the kit."""
        ...

    async def insert_role(
        self, user_id: uuid.UUID, values: Values, events: Sequence[NewRoleEvent]
    ) -> RoleRecord: ...

    async def write_role(self, user_id: uuid.UUID, change: RoleWrite) -> RoleRecord:
        """Checks `change.version` when set; bumps `version` when any value changes (events alone
        don't). Raises `NotFound` or `Conflict(current_version=...)`."""
        ...

    async def column_ranks(self, user_id: uuid.UUID, status: str) -> list[tuple[uuid.UUID, str]]:
        """`(id, rank)` of live roles in one status, in board order."""
        ...

    async def set_ranks(self, user_id: uuid.UUID, ranks: Mapping[uuid.UUID, str]) -> None: ...

    async def list_events(
        self,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        kinds: Sequence[str],
        offset: int,
        limit: int,
    ) -> list[RoleEventRecord]:
        """Newest first, only the given kinds."""
        ...


@dataclass(frozen=True, kw_only=True)
class TextSearch:
    """A case-insensitive substring match (`q`) over some text columns. `term` is taken
    literally: adapters escape any LIKE wildcards. With `full_name`, "first_name last_name"
    is matched too."""

    term: str
    columns: tuple[str, ...]
    full_name: bool = False


class VersionedStore[R](Protocol):
    """Contacts and targets: equality filters (a tuple value means "one of"), an optional text
    search, soft delete, optimistic `version`."""

    async def list_rows(
        self,
        user_id: uuid.UUID,
        filters: Values,
        offset: int,
        limit: int,
        search: TextSearch | None = None,
    ) -> list[R]: ...

    async def get_row(self, user_id: uuid.UUID, row_id: uuid.UUID) -> R | None: ...

    async def insert_row(self, user_id: uuid.UUID, values: Values) -> R:
        """Raises `ValidationFailed` on a duplicate natural key (contact URL, target name)."""
        ...

    async def update_row(
        self, user_id: uuid.UUID, row_id: uuid.UUID, version: int, values: Values
    ) -> R:
        """Raises `NotFound` or `Conflict(current_version=...)`."""
        ...


class ContactStore(VersionedStore[ContactRecord], Protocol): ...


class TargetStore(VersionedStore[TargetRecord], Protocol): ...


class KitStore(Protocol):
    async def save_kit(self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values) -> KitRecord:
        """Insert or replace the role's kit; bumps its `version`."""
        ...


class ChatStore(Protocol):
    async def list_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, offset: int, limit: int
    ) -> list[ChatRecord]:
        """Oldest first."""
        ...

    async def recent_messages(
        self, user_id: uuid.UUID, role_id: uuid.UUID, limit: int
    ) -> list[ChatRecord]:
        """The newest `limit` messages, returned oldest first."""
        ...

    async def add_message(self, user_id: uuid.UUID, message: ChatRecord) -> None: ...

    async def clear(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None: ...


class RejectionStore(Protocol):
    async def get_rejection(
        self, user_id: uuid.UUID, role_id: uuid.UUID
    ) -> RejectionRecord | None: ...

    async def update_rejection(
        self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values
    ) -> RejectionRecord: ...

    async def list_rejections(self, user_id: uuid.UUID, limit: int) -> list[RejectionRecord]:
        """The caller's rejections on live roles, most recently rejected first."""
        ...


class FollowupStore(Protocol):
    async def replace_suggestions(
        self, user_id: uuid.UUID, role_id: uuid.UUID, rows: Sequence[Values]
    ) -> list[FollowupRecord]:
        """Drop the role's unsent suggestions and insert `rows` (sent ones are kept)."""
        ...


class ResearchStore(Protocol):
    async def create_run(self, user_id: uuid.UUID, values: Values) -> ResearchRunRecord: ...

    async def get_run(self, user_id: uuid.UUID, run_id: uuid.UUID) -> ResearchRunRecord | None: ...

    async def update_run(
        self, user_id: uuid.UUID, run_id: uuid.UUID, values: Values
    ) -> ResearchRunRecord: ...

    async def add_step(self, user_id: uuid.UUID, step: StepRecord) -> None: ...

    async def steps_after(
        self, user_id: uuid.UUID, run_id: uuid.UUID, seq: int
    ) -> list[StepRecord]: ...


class ProfileStore(Protocol):
    async def load_profile(self, user_id: uuid.UUID) -> CandidateProfile: ...


@dataclass(frozen=True)
class Released:
    segment_id: int
    text: str


@dataclass(frozen=True)
class Retracted:
    segment_id: int
    reason: str


type FilterEvent = Released | Retracted


class SegmentFilter(Protocol):
    """The streaming redactor for one stream (ARCH-30): deltas in, segment events out."""

    @property
    def visible_text(self) -> str: ...

    async def feed(self, chunk: str) -> list[FilterEvent]: ...

    async def finish(self) -> list[FilterEvent]: ...


class Redaction(Protocol):
    """`app.guards.redaction` behind a port, so domain code can release only filtered text."""

    def open_stream(self) -> SegmentFilter: ...

    async def redact_step(self, text: str) -> str | None:
        """Whole-payload filter that fails closed: `None` means "show the step type only"."""
        ...


@dataclass(frozen=True, kw_only=True)
class RoleFact:
    id: uuid.UUID
    status: str
    found_at: datetime
    posted_at: datetime | None = None
    applied_at: datetime | None = None
    followed_up_at: datetime | None = None
    score: int | None = None
    stage: str | None = None
    source: str = "manual"
    track: str | None = None


@dataclass(frozen=True, kw_only=True)
class StatusFact:
    role_id: uuid.UUID
    at: datetime
    to_value: str


@dataclass(frozen=True, kw_only=True)
class RejectionFact:
    at: datetime
    categories: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class CallFact:
    at: datetime
    feature: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True, kw_only=True)
class ColumnEntry:
    task_id: uuid.UUID
    at: datetime
    column: str


@dataclass(frozen=True, kw_only=True)
class ReviewFact:
    at: datetime
    mode: str
    is_passed: bool


@dataclass(frozen=True, kw_only=True)
class BoardFacts:
    entries: tuple[ColumnEntry, ...] = ()
    reviews: tuple[ReviewFact, ...] = ()
    live_columns: tuple[str, ...] = field(default=())


@dataclass(frozen=True, kw_only=True)
class TaskFact:
    id: uuid.UUID
    column: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class RunFact:
    task_id: uuid.UUID
    kind: str
    status: str
    round: int
    cost_usd: float
    created_at: datetime
    outcome: str | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class TriageFact:
    task_id: uuid.UUID
    at: datetime
    in_scope: bool


@dataclass(frozen=True, kw_only=True)
class DevBoardFacts:
    """Live tasks and their column entries; every run and triage record."""

    tasks: tuple[TaskFact, ...] = ()
    entries: tuple[ColumnEntry, ...] = ()
    runs: tuple[RunFact, ...] = ()
    triage: tuple[TriageFact, ...] = ()


class DashboardSource(Protocol):
    """Raw facts; every aggregate is computed in `app.domain.jobs.dashboard`."""

    async def roles(self, user_id: uuid.UUID) -> list[RoleFact]: ...

    async def status_events(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[StatusFact]: ...

    async def rejections(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[RejectionFact]: ...

    async def llm_calls(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[CallFact]: ...

    async def board(self, user_id: uuid.UUID, end: datetime) -> BoardFacts:
        """Column entries before `end` (cycle time needs the entry before the range too)."""
        ...

    async def dev_board(self, user_id: uuid.UUID) -> DevBoardFacts: ...


class RoleScorer(Protocol):
    """Scores a role for its owner outside a request (the feed-replay job). Raises the typed
    errors of scoring (`BudgetExceeded`, `NotFound`, ...)."""

    async def score_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None: ...


@dataclass(frozen=True, kw_only=True)
class CampaignFacts:
    """What the plan's actuals are counted from, inside one date range."""

    activity: tuple[tuple[date, str], ...] = ()
    applied: tuple[datetime, ...] = ()
    followups: tuple[datetime, ...] = ()


@dataclass(frozen=True, kw_only=True)
class MainPageFacts:
    """Counts for the main page's extra tabs, hints and alerts."""

    outreach_due: int = 0
    feedback_due: int = 0
    followups_due: int = 0
    latest_feed_role_at: datetime | None = None


class CampaignPlanSource(Protocol):
    def load(self) -> Mapping[str, object]:
        """The raw plan (parsed YAML). Raises `ValueError` when it can't be read."""
        ...


class CampaignStore(Protocol):
    async def list_activity(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> list[ActivityRecord]:
        """`start <= on_date < end`, newest first."""
        ...

    async def add_activity(self, user_id: uuid.UUID, values: Values) -> ActivityRecord: ...

    async def delete_activity(self, user_id: uuid.UUID, activity_id: uuid.UUID) -> bool: ...

    async def done_tasks(self, user_id: uuid.UUID, week: int) -> set[str]: ...

    async def set_task_done(
        self, user_id: uuid.UUID, task_id: str, week: int, at: datetime
    ) -> None:
        """Idempotent."""
        ...

    async def clear_task_done(self, user_id: uuid.UUID, task_id: str, week: int) -> None: ...

    async def note(self, user_id: uuid.UUID, key: str) -> str | None: ...

    async def facts(self, user_id: uuid.UUID, start: date, end: date) -> CampaignFacts:
        """Activity with `start <= on_date < end`; applied and follow-up times from `start`
        (UTC midnight) to `end` (exclusive), on live roles."""
        ...

    async def main_page(self, user_id: uuid.UUID, followup_before: datetime) -> MainPageFacts:
        """`followups_due`: applied roles not followed up, applied before `followup_before`."""
        ...

    async def rejection_summaries(
        self, user_id: uuid.UUID, offset: int, limit: int
    ) -> list[RejectionSummaryRecord]:
        """Rejections on live roles, most recently rejected first."""
        ...
