"""Facts for the maintainer health panel: job runs, LLM calls and first-party site usage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class JobRunFact:
    """The latest finished run of one job (`job` as stored, e.g. `scan`)."""

    job: str
    started_at: datetime
    ok: bool
    error_code: str | None = None


@dataclass(frozen=True, kw_only=True)
class UsageCallFact:
    at: datetime
    feature: str
    provider: str
    cost_usd: float
    fallback_used: bool


@dataclass(frozen=True, kw_only=True)
class SiteUsage:
    page_views: int
    sessions: int
    vitals_p75: tuple[tuple[str, float], ...]


class HealthSource(Protocol):
    async def last_job_runs(self, jobs: tuple[str, ...]) -> list[JobRunFact]:
        """The latest finished run of each named job; jobs that never finished are absent."""
        ...

    async def llm_calls(self, user_id: uuid.UUID, start: datetime) -> list[UsageCallFact]:
        """The caller's LLM calls (row-level security applies) at or after `start`."""
        ...

    async def site_usage(self, start: datetime) -> SiteUsage:
        """Page views, distinct page-view sessions and per-metric Web Vitals p75 since `start`."""
        ...
