"""In-memory `HealthSource` fake (ARCH-04) over the in-memory usage and web-event stores."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

from app.adapters.fakes.data import InMemoryUsageStore, InMemoryWebEvents, percentile
from app.domain.ports.health import JobRunFact, SiteUsage, UsageCallFact


class MemoryHealth:
    """`job_runs` holds every finished run; tests append to it directly."""

    def __init__(
        self, usage: InMemoryUsageStore | None = None, web: InMemoryWebEvents | None = None
    ) -> None:
        self._usage = usage
        self._web = web
        self.job_runs: list[JobRunFact] = []

    async def last_job_runs(self, jobs: tuple[str, ...]) -> list[JobRunFact]:
        latest: dict[str, JobRunFact] = {}
        for run in self.job_runs:
            seen = latest.get(run.job)
            if run.job in jobs and (seen is None or run.started_at > seen.started_at):
                latest[run.job] = run
        return list(latest.values())

    async def llm_calls(self, user_id: uuid.UUID, start: datetime) -> list[UsageCallFact]:
        calls = self._usage.calls if self._usage is not None else []
        return [
            UsageCallFact(
                at=call.at,
                feature=call.feature.value,
                provider=call.provider,
                cost_usd=call.cost_usd,
                fallback_used=call.is_fallback,
            )
            for call in calls
            if call.user_id == user_id and call.at >= start
        ]

    async def site_usage(self, start: datetime) -> SiteUsage:
        events = [e for e in (self._web.events if self._web else []) if e.at >= start]
        views = [e for e in events if e.type == "page_view"]
        vitals: dict[str, list[float]] = defaultdict(list)
        for event in events:
            if event.type == "web_vital" and event.metric and event.value is not None:
                vitals[event.metric].append(event.value)
        return SiteUsage(
            page_views=len(views),
            sessions=len({e.session_id for e in views}),
            vitals_p75=tuple((m, percentile(v, 0.75)) for m, v in sorted(vitals.items())),
        )
