"""Dashboard KPIs and chart series (spec "Dashboard"). Every number is computed here from raw
facts, so the in-memory fake and Postgres give the same answers and tests can check each one
against an independent SQL query.

Definitions:
- open role: status `new`, `reviewing`, `applied`, `interview` or `offer` (not deleted).
- interview rate: of roles applied in the range, the share that reached `interview` or `offer`.
- funnel: roles that reached each status in the range (status events); `new` also counts
  roles found in the range.
- cycle time: time from entering a column to the next move, for moves that end in the range.
- agent pass rate: first-round (`round = 0`) review runs finished in the range that passed.
- tasks in flight: live cards outside `backlog`, `out_of_scope` and `done`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.domain.identity import Principal
from app.domain.jobs.board_columns import NOT_IN_FLIGHT, TASK_COLUMNS, cycle_samples
from app.domain.jobs.dev_metrics import DevMetricsView, dev_metrics
from app.domain.jobs.insights import EPOCH, InsightsView, job_insights
from app.domain.jobs.metrics import (
    DateRange,
    bucket_start,
    hours_between,
    percentile,
    rounded,
)
from app.domain.jobs.ports import CampaignStore, DashboardSource, RoleFact
from app.domain.jobs.summary import JobSummaryView, job_summary
from app.domain.money import DEFAULT_USD_TO_AUD, to_aud
from app.domain.ports import Clock
from app.domain.user_settings import PrefsSource, prefs_for

__all__ = ["NOT_IN_FLIGHT", "TASK_COLUMNS", "DashboardService"]

ROLE_STATUSES = (
    "new",
    "reviewing",
    "applied",
    "interview",
    "offer",
    "passed",
    "closed",
    "rejected",
    "unknown",
)
OPEN_STATUSES = frozenset({"new", "reviewing", "applied", "interview", "offer"})
INTERVIEW_STATUSES = frozenset({"interview", "offer"})
REJECTION_CATEGORIES = (
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
)
FAR_FUTURE = datetime(9000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, kw_only=True)
class KpiView:
    open_roles: int
    applied_in_range: int
    llm_spend_aud: float
    tasks_in_flight: int
    as_of: datetime
    interview_rate: float | None = None
    median_post_to_apply_hours: float | None = None
    agent_pass_rate: float | None = None


@dataclass(frozen=True)
class Count:
    key: str
    count: int


@dataclass(frozen=True, kw_only=True)
class PeriodHours:
    period_start: date
    count: int
    median_hours: float | None


@dataclass(frozen=True, kw_only=True)
class PostToApplyView:
    items: tuple[PeriodHours, ...]
    median_hours: float | None
    p75_hours: float | None


@dataclass(frozen=True)
class ScoreBin:
    low: int
    high: int
    count: int


@dataclass(frozen=True, kw_only=True)
class SpendRow:
    period_start: date
    feature: str
    provider: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_aud: float


@dataclass(frozen=True, kw_only=True)
class CycleRow:
    column: str
    samples: int
    median_hours: float | None
    p75_hours: float | None


@dataclass(frozen=True, kw_only=True)
class PassRow:
    period_start: date
    mode: str
    runs: int
    passed: int
    pass_rate: float | None


def _applied(roles: Sequence[RoleFact], window: DateRange) -> list[RoleFact]:
    return [role for role in roles if window.contains(role.applied_at)]


def _post_to_apply_hours(role: RoleFact) -> float | None:
    if role.posted_at is None or role.applied_at is None or role.applied_at < role.posted_at:
        return None
    return hours_between(role.posted_at, role.applied_at)


class DashboardService:
    def __init__(
        self,
        source: DashboardSource,
        clock: Clock,
        main_page: CampaignStore | None = None,
        prefs: PrefsSource | None = None,
        usd_to_aud: float = DEFAULT_USD_TO_AUD,
    ) -> None:
        self._usd_to_aud = usd_to_aud
        self._prefs = prefs
        self._main_page = main_page
        self._source = source
        self._clock = clock

    async def kpis(self, principal: Principal, window: DateRange) -> KpiView:
        user_id = principal.user_id
        roles = await self._source.roles(user_id)
        applied = _applied(roles, window)
        reached = {
            event.role_id
            for event in await self._source.status_events(user_id, window.start_at, FAR_FUTURE)
            if event.to_value in INTERVIEW_STATUSES
        }
        interviewed = [r for r in applied if r.id in reached or r.status in INTERVIEW_STATUSES]
        hours = [h for h in map(_post_to_apply_hours, applied) if h is not None]
        calls = await self._source.llm_calls(user_id, window.start_at, window.end_at)
        board = await self._source.board(user_id, FAR_FUTURE)
        reviews = [review for review in board.reviews if window.contains(review.at)]
        passed = sum(1 for review in reviews if review.is_passed)
        return KpiView(
            open_roles=sum(1 for role in roles if role.status in OPEN_STATUSES),
            applied_in_range=len(applied),
            interview_rate=rounded(len(interviewed) / len(applied), 4) if applied else None,
            median_post_to_apply_hours=rounded(percentile(hours, 0.5)),
            llm_spend_aud=to_aud(sum(call.cost_usd for call in calls), self._usd_to_aud),
            agent_pass_rate=rounded(passed / len(reviews), 4) if reviews else None,
            tasks_in_flight=sum(1 for c in board.live_columns if c not in NOT_IN_FLIGHT),
            as_of=self._clock.now(),
        )

    async def funnel(self, principal: Principal, window: DateRange) -> list[Count]:
        user_id = principal.user_id
        reached: dict[str, set[object]] = defaultdict(set)
        for event in await self._source.status_events(user_id, window.start_at, window.end_at):
            reached[event.to_value].add(event.role_id)
        for role in await self._source.roles(user_id):
            if window.contains(role.found_at):
                reached["new"].add(role.id)
        return [Count(status, len(reached[status])) for status in ROLE_STATUSES]

    async def post_to_apply(self, principal: Principal, window: DateRange) -> PostToApplyView:
        applied = _applied(await self._source.roles(principal.user_id), window)
        by_period: dict[date, list[float]] = defaultdict(list)
        every: list[float] = []
        for role in applied:
            hours = _post_to_apply_hours(role)
            if hours is not None and role.applied_at is not None:
                by_period[bucket_start(role.applied_at, window.bucket)].append(hours)
                every.append(hours)
        items = tuple(
            PeriodHours(
                period_start=period,
                count=len(values),
                median_hours=rounded(percentile(values, 0.5)),
            )
            for period, values in sorted(by_period.items())
        )
        return PostToApplyView(
            items=items,
            median_hours=rounded(percentile(every, 0.5)),
            p75_hours=rounded(percentile(every, 0.75)),
        )

    async def score_distribution(self, principal: Principal, window: DateRange) -> list[ScoreBin]:
        counts: Counter[int] = Counter()
        for role in await self._source.roles(principal.user_id):
            if role.score is not None and window.contains(role.found_at):
                counts[min(role.score // 10, 9)] += 1
        return [ScoreBin(b * 10, 100 if b == 9 else b * 10 + 9, counts[b]) for b in range(10)]

    async def rejection_reasons(self, principal: Principal, window: DateRange) -> list[Count]:
        facts = await self._source.rejections(principal.user_id, window.start_at, window.end_at)
        counts = Counter(category for fact in facts for category in set(fact.categories))
        order = {category: index for index, category in enumerate(REJECTION_CATEGORIES)}
        known = [c for c in counts if c in order]
        known.sort(key=lambda category: (-counts[category], order[category]))
        return [Count(category, counts[category]) for category in known]

    async def llm_spend(self, principal: Principal, window: DateRange) -> list[SpendRow]:
        calls = await self._source.llm_calls(principal.user_id, window.start_at, window.end_at)
        groups: dict[tuple[date, str, str], list[float]] = defaultdict(lambda: [0, 0, 0, 0.0])
        for call in calls:
            totals = groups[(bucket_start(call.at, window.bucket), call.feature, call.provider)]
            totals[0] += 1
            totals[1] += call.input_tokens
            totals[2] += call.output_tokens
            totals[3] += call.cost_usd
        return [
            SpendRow(
                period_start=period,
                feature=feature,
                provider=provider,
                calls=int(totals[0]),
                input_tokens=int(totals[1]),
                output_tokens=int(totals[2]),
                cost_aud=to_aud(totals[3], self._usd_to_aud),
            )
            for (period, feature, provider), totals in sorted(groups.items())
        ]

    async def cycle_time(self, principal: Principal, window: DateRange) -> list[CycleRow]:
        board = await self._source.board(principal.user_id, window.end_at)
        samples = cycle_samples(board.entries, window)
        return [
            CycleRow(
                column=column,
                samples=len(samples.get(column, [])),
                median_hours=rounded(percentile(samples.get(column, []), 0.5)),
                p75_hours=rounded(percentile(samples.get(column, []), 0.75)),
            )
            for column in TASK_COLUMNS
        ]

    async def agent_pass_rate(self, principal: Principal, window: DateRange) -> list[PassRow]:
        board = await self._source.board(principal.user_id, FAR_FUTURE)
        groups: dict[tuple[date, str], list[int]] = defaultdict(lambda: [0, 0])
        for review in board.reviews:
            if window.contains(review.at):
                totals = groups[(bucket_start(review.at, window.bucket), review.mode)]
                totals[0] += 1
                totals[1] += int(review.is_passed)
        return [
            PassRow(
                period_start=period,
                mode=mode,
                runs=runs,
                passed=passed,
                pass_rate=rounded(passed / runs, 4) if runs else None,
            )
            for (period, mode), (runs, passed) in sorted(groups.items())
        ]

    async def job_summary(self, principal: Principal) -> JobSummaryView:
        now = self._clock.now()
        after = (await prefs_for(self._prefs, principal.user_id)).followups.after
        extras = None
        if self._main_page is not None:
            extras = await self._main_page.main_page(principal.user_id, now - after)
        return job_summary(await self._source.roles(principal.user_id), now, extras, after)

    async def insights(self, principal: Principal, window: DateRange) -> InsightsView:
        user_id = principal.user_id
        roles = await self._source.roles(user_id)
        events = await self._source.status_events(user_id, EPOCH, FAR_FUTURE)
        prefs = await prefs_for(self._prefs, user_id)
        return job_insights(roles, events, window, prefs, self._clock.now())

    async def dev_metrics(self, principal: Principal, window: DateRange) -> DevMetricsView:
        facts = await self._source.dev_board(principal.user_id)
        return dev_metrics(facts, window, self._clock.now())
