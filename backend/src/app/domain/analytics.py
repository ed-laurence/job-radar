"""First-party web analytics: strict ingest and the maintainer summary.

Events carry no user id, IP, user agent or free text. The only identity stored is a role tag.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.domain.errors import BadRequest
from app.domain.identity import AppRole
from app.domain.ports import Clock, PageViews, StoredWebEvent, VitalSample, WebEventsRepository

# Events stamped too far from server time are dropped, not stored: they would land in the
# wrong partition and skew the daily rollups.
MAX_EVENT_AGE = timedelta(days=2)
MAX_CLOCK_SKEW = timedelta(minutes=5)
DEFAULT_WINDOW = timedelta(days=90)
MAX_WINDOW = timedelta(days=366)

# Core Web Vitals "good" thresholds (ms, except CLS which is unitless).
VITAL_BUDGETS: dict[str, float] = {
    "LCP": 2500.0,
    "INP": 200.0,
    "CLS": 0.1,
    "FCP": 1800.0,
    "TTFB": 800.0,
}

FUNNELS: dict[str, tuple[str, ...]] = {
    "application": ("role_created", "role_moved", "kit_generated"),
    "dev_agent": ("card_moved", "agent_run_started", "agent_run_approved"),
}


@dataclass(frozen=True)
class WebEventInput:
    type: str
    route: str
    at: datetime
    session_id: str
    name: str | None = None
    metric: str | None = None
    value: float | None = None
    rating: str | None = None


@dataclass(frozen=True)
class FunnelStep:
    name: str
    sessions: int


@dataclass(frozen=True)
class FunnelView:
    name: str
    steps: tuple[FunnelStep, ...]


@dataclass(frozen=True)
class VitalView:
    route: str
    metric: str
    p75: float
    samples: int
    budget: float | None
    is_over_budget: bool | None


@dataclass(frozen=True)
class AnalyticsSummaryView:
    views_by_page: tuple[PageViews, ...]
    funnels: tuple[FunnelView, ...]
    web_vitals: tuple[VitalView, ...]


class AnalyticsService:
    def __init__(self, repository: WebEventsRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def ingest(self, events: Sequence[WebEventInput], role: AppRole) -> int:
        now = self._clock.now()
        fresh = [
            event for event in events if now - MAX_EVENT_AGE <= event.at <= now + MAX_CLOCK_SKEW
        ]
        if not fresh:
            return 0
        return await self._repository.insert_many([_to_stored(event, role) for event in fresh])

    async def summary(self, start: date | None, end: date | None) -> AnalyticsSummaryView:
        start, end = self._window(start, end)
        views = await self._repository.page_views(start, end)
        pairs = await self._repository.product_event_sessions(start, end)
        vitals = await self._repository.vitals_p75(start, end)
        return AnalyticsSummaryView(
            views_by_page=tuple(sorted(views, key=lambda view: (-view.views, view.route))[:200]),
            funnels=build_funnels(pairs),
            web_vitals=tuple(_vital_view(sample) for sample in vitals[:500]),
        )

    def _window(self, start: date | None, end: date | None) -> tuple[date, date]:
        today = self._clock.now().date()
        resolved_end = end or today + timedelta(days=1)
        resolved_start = start or today - DEFAULT_WINDOW
        if resolved_start >= resolved_end:
            raise BadRequest("`from` must be before `to`.")
        if resolved_end - resolved_start > MAX_WINDOW:
            raise BadRequest("The date range can be at most 366 days.")
        return resolved_start, resolved_end


def build_funnels(pairs: Sequence[tuple[str, str]]) -> tuple[FunnelView, ...]:
    """A session counts for a step when it also reached every earlier step."""
    sessions_by_name: dict[str, set[str]] = defaultdict(set)
    for session_id, name in pairs:
        sessions_by_name[name].add(session_id)
    views: list[FunnelView] = []
    for funnel_name, step_names in FUNNELS.items():
        reached: set[str] | None = None
        steps: list[FunnelStep] = []
        for step_name in step_names:
            here = sessions_by_name.get(step_name, set())
            reached = set(here) if reached is None else reached & here
            steps.append(FunnelStep(name=step_name, sessions=len(reached)))
        views.append(FunnelView(name=funnel_name, steps=tuple(steps)))
    return tuple(views)


def _vital_view(sample: VitalSample) -> VitalView:
    budget = VITAL_BUDGETS.get(sample.metric)
    return VitalView(
        route=sample.route,
        metric=sample.metric,
        p75=sample.p75,
        samples=sample.samples,
        budget=budget,
        is_over_budget=None if budget is None else sample.p75 > budget,
    )


def _to_stored(event: WebEventInput, role: AppRole) -> StoredWebEvent:
    return StoredWebEvent(
        at=event.at,
        day=event.at.astimezone(UTC).date(),
        session_id=event.session_id,
        role_tag=role.value,
        type=event.type,
        route=event.route,
        name=event.name,
        metric=event.metric,
        value=event.value,
        rating=event.rating,
    )
