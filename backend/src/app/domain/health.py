"""Maintainer health panel (spec `getHealthSummary`): scheduled jobs, LLM spend, site usage.

Definitions:
- jobs: the latest finished run of each scheduled job (`never` when none has finished); the next
  run is the next tick of the job's schedule after now. Schedules mirror the Cloud Scheduler
  defaults in `infra/envs/prod/variables.tf` (UTC).
- llm: the maintainer's calls (row-level security applies; scheduled jobs act as the
  maintainer). Today and month are UTC calendar periods; the by-feature breakdown is month to
  date; the fallback rate is over the last 7 days. The monthly budget is the caller's
  `llm.monthly_budget_aud` setting, else config (`None` = none). Money is AUD: the calls'
  USD cost converted at `usd_to_aud` (`app.domain.money`).
- site: page views and distinct page-view sessions over the last 7 days, and the p75 of each
  Web Vitals metric over the same window, with the Core Web Vitals budgets.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from app.domain.analytics import VITAL_BUDGETS
from app.domain.devboard.schedule import JobName
from app.domain.identity import Principal
from app.domain.money import DEFAULT_USD_TO_AUD, to_aud
from app.domain.ports import Clock
from app.domain.ports.health import HealthSource, JobRunFact, UsageCallFact

WINDOW = timedelta(days=7)
VITALS_ORDER = ("LCP", "INP", "CLS", "FCP", "TTFB")
MAX_BREAKDOWN = 50


@dataclass(frozen=True, kw_only=True)
class Schedule:
    """A cron subset: every `every_minutes`, or daily at `hour`, or weekly on `weekday` too."""

    every_minutes: int | None = None
    hour: int = 0
    minute: int = 0
    weekday: int | None = None  # Monday = 0

    def next_after(self, now: datetime) -> datetime:
        now = now.astimezone(UTC)
        if self.every_minutes is not None:
            midnight = datetime.combine(now.date(), time.min, tzinfo=UTC)
            elapsed = int((now - midnight).total_seconds() // 60)
            ticks = elapsed // self.every_minutes + 1
            return midnight + timedelta(minutes=ticks * self.every_minutes)
        tick = datetime.combine(now.date(), time(self.hour, self.minute), tzinfo=UTC)
        step = timedelta(days=1)
        if self.weekday is not None:
            tick += timedelta(days=(self.weekday - tick.weekday()) % 7)
            step = timedelta(days=7)
        return tick if tick > now else tick + step


SCHEDULES: dict[JobName, Schedule] = {
    JobName.SCAN: Schedule(every_minutes=15),  # */15 * * * *
    JobName.DIGEST: Schedule(hour=20),  # 0 20 * * *
    JobName.ROLLUP: Schedule(hour=21),  # 0 21 * * *
    JobName.WATCHDOG: Schedule(every_minutes=15),  # */15 * * * *
}


@dataclass(frozen=True, kw_only=True)
class JobHealth:
    name: str
    last_status: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class FeatureSpend:
    feature: str
    provider: str
    calls: int
    spend_aud: float


@dataclass(frozen=True, kw_only=True)
class LlmHealth:
    spend_today_aud: float
    spend_month_aud: float
    budget_month_aud: float | None
    calls_today: int
    fallback_rate_7d: float | None
    by_feature: tuple[FeatureSpend, ...]


@dataclass(frozen=True, kw_only=True)
class VitalHealth:
    metric: str
    value: float
    budget: float | None


@dataclass(frozen=True, kw_only=True)
class SiteHealth:
    page_views_7d: int
    sessions_7d: int
    web_vitals_p75: tuple[VitalHealth, ...]


@dataclass(frozen=True, kw_only=True)
class HealthView:
    jobs: tuple[JobHealth, ...]
    llm: LlmHealth
    site: SiteHealth
    as_of: datetime


def job_health(runs: Sequence[JobRunFact], now: datetime) -> tuple[JobHealth, ...]:
    latest = {run.job: run for run in runs}
    views: list[JobHealth] = []
    for job, schedule in SCHEDULES.items():
        run = latest.get(job.value)
        views.append(
            JobHealth(
                name=job.value.replace("-", "_"),
                last_status="never" if run is None else ("ok" if run.ok else "failed"),
                last_run_at=None if run is None else run.started_at,
                next_run_at=schedule.next_after(now),
            )
        )
    return tuple(views)


def month_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date().replace(day=1), time.min, tzinfo=UTC)


def llm_health(
    calls: Sequence[UsageCallFact],
    now: datetime,
    budget_month_aud: float | None,
    usd_to_aud: float = DEFAULT_USD_TO_AUD,
) -> LlmHealth:
    today = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    month = [call for call in calls if month_start(now) <= call.at <= now]
    day = [call for call in month if call.at >= today]
    recent = [call for call in calls if now - WINDOW <= call.at <= now]
    groups: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0])
    for call in month:
        totals = groups[(call.feature, call.provider)]
        totals[0] += 1
        totals[1] += call.cost_usd
    breakdown = sorted(
        (
            FeatureSpend(feature=f, provider=p, calls=int(n), spend_aud=to_aud(cost, usd_to_aud))
            for (f, p), (n, cost) in groups.items()
        ),
        key=lambda row: (-row.spend_aud, row.feature, row.provider),
    )
    fallbacks = sum(1 for call in recent if call.fallback_used)
    return LlmHealth(
        spend_today_aud=to_aud(sum(call.cost_usd for call in day), usd_to_aud),
        spend_month_aud=to_aud(sum(call.cost_usd for call in month), usd_to_aud),
        budget_month_aud=budget_month_aud,
        calls_today=len(day),
        fallback_rate_7d=round(fallbacks / len(recent), 4) if recent else None,
        by_feature=tuple(breakdown[:MAX_BREAKDOWN]),
    )


class BudgetLookup(Protocol):
    async def monthly_budget(self, user_id: uuid.UUID) -> float | None:
        """The caller's effective `llm.monthly_budget_aud` (settings, else the config), in AUD."""
        ...


class HealthService:
    def __init__(
        self,
        source: HealthSource,
        clock: Clock,
        budget_month_aud: float | None = None,
        budgets: BudgetLookup | None = None,
        usd_to_aud: float = DEFAULT_USD_TO_AUD,
    ) -> None:
        self._source = source
        self._clock = clock
        self._budget = budget_month_aud
        self._budgets = budgets
        self._usd_to_aud = usd_to_aud

    async def summary(self, principal: Principal) -> HealthView:
        now = self._clock.now()
        runs = await self._source.last_job_runs(tuple(job.value for job in SCHEDULES))
        since = min(month_start(now), now - WINDOW)
        calls = await self._source.llm_calls(principal.user_id, since)
        usage = await self._source.site_usage(now - WINDOW)
        p75 = dict(usage.vitals_p75)
        budget = self._budget
        if self._budgets is not None:
            budget = await self._budgets.monthly_budget(principal.user_id)
        return HealthView(
            jobs=job_health(runs, now),
            llm=llm_health(calls, now, budget, self._usd_to_aud),
            site=SiteHealth(
                page_views_7d=usage.page_views,
                sessions_7d=usage.sessions,
                web_vitals_p75=tuple(
                    VitalHealth(metric=m, value=round(p75[m], 4), budget=VITAL_BUDGETS.get(m))
                    for m in VITALS_ORDER
                    if m in p75
                ),
            ),
            as_of=now,
        )
