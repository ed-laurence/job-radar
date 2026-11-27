"""The weekly campaign plan (ported from job-seeker's `campaign.py`): week maths, targets,
actuals and the task checklist. The plan itself comes from YAML (`CAMPAIGN_CONFIG`); per-user
state (activity, done marks) is under row-level security.

Week maths: the plan starts on a Monday; week 1 is the seven days from it. A date's week is
`(date - start).days // 7 + 1`, so dates before the start fall in week 0 or below. All dates are
UTC calendar dates.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, timedelta
from typing import cast

from app.domain.errors import FieldError, NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.ports import CampaignFacts, CampaignPlanSource, CampaignStore
from app.domain.jobs.records import ActivityRecord
from app.domain.jobs.roles import RoleService
from app.domain.ports import Clock, Ids
from app.domain.user_settings import PrefsSource, prefs_for

MAX_WEEKS = 52
# Written by the seed job: the seeded demo's anchor date, so a relative plan start lines up
# with the seeded activity.
SEED_ANCHOR_NOTE = "seed_anchor"

KINDS = (
    "agency_call",
    "submitted",
    "network_message",
    "linkedin_invite",
    "conversation",
    "direct_approach",
    "screen",
    "interview",
    "offer",
    "note",
)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    kinds: tuple[str, ...] = ()


# Scorecard rows, in order. `kinds` are the logged activity kinds counted towards the metric;
# applications and follow-ups are counted automatically from roles.
METRICS = (
    Metric("agency_calls", "Recruiter conversations", ("agency_call",)),
    Metric("submissions", "Put forward by a recruiter", ("submitted",)),
    Metric("network_messages", "Network messages", ("network_message",)),
    Metric("invites", "Invites", ("linkedin_invite",)),
    Metric("conversations", "Conversations", ("conversation",)),
    Metric("direct_approaches", "Direct approaches", ("direct_approach",)),
    Metric("applications", "Applications"),
    Metric("followups", "Follow-ups"),
    Metric("screens", "Screens", ("screen",)),
)
METRIC_KEYS = frozenset(m.key for m in METRICS)
_METRIC_OF_KIND = {kind: m.key for m in METRICS for kind in m.kinds}


@dataclass(frozen=True, kw_only=True)
class PlanTask:
    id: str
    title: str
    weeks: frozenset[int]
    when: str | None = None
    steps: str | None = None
    links: tuple[tuple[str, str], ...] = ()
    metric: str | None = None


@dataclass(frozen=True, kw_only=True)
class Checkpoint:
    week: int
    text: str


@dataclass(frozen=True, kw_only=True)
class Plan:
    name: str
    weeks: int
    start: date | None = None
    start_offset_days: int = 0
    targets: Mapping[str, int]
    week_targets: Mapping[int, Mapping[str, int]]
    checkpoints: tuple[Checkpoint, ...] = ()
    tasks: tuple[PlanTask, ...] = ()


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_index(day: date, start: date) -> int:
    """1 = the week starting `start`; 0 and below are before the plan."""
    return (day - start).days // 7 + 1


def week_bounds(start: date, week: int) -> tuple[date, date]:
    """First and last day (inclusive) of a plan week."""
    first = start + timedelta(days=7 * (week - 1))
    return first, first + timedelta(days=6)


def clamp_week(week: int | None, current: int, total: int) -> int:
    return min(max(week if week is not None else current, 1), total)


def task_weeks(spec: object, total: int) -> frozenset[int]:
    """`all`, a list of week numbers, a single number, or a range string like "2-7"."""
    if spec is None or spec == "all":
        return frozenset(range(1, total + 1))
    if isinstance(spec, bool):
        raise TypeError("weeks must be all, a list, a number or a range")
    if isinstance(spec, int):
        return frozenset({spec})
    if isinstance(spec, str):
        low, sep, high = spec.partition("-")
        if not sep:
            return frozenset({int(low)})
        return frozenset(range(int(low), int(high) + 1))
    if isinstance(spec, list):
        return frozenset(int(cast("int | str", item)) for item in cast("list[object]", spec))
    raise TypeError("weeks must be all, a list, a number or a range")


def targets_for(plan: Plan, week: int) -> dict[str, int]:
    return {**plan.targets, **plan.week_targets.get(week, {})}


def plan_start(plan: Plan, anchor: date) -> date:
    """An explicit start, else `start_offset_days` from the anchor; always a Monday."""
    start = plan.start or anchor + timedelta(days=plan.start_offset_days)
    return monday(start)


def count_actuals(facts: CampaignFacts) -> dict[str, int]:
    """Per metric key, everything in `facts` (already limited to one week)."""
    counts: Counter[str] = Counter()
    for _, kind in facts.activity:
        if kind in _METRIC_OF_KIND:
            counts[_METRIC_OF_KIND[kind]] += 1
    counts["applications"] += len(facts.applied)
    counts["followups"] += len(facts.followups)
    return {m.key: counts[m.key] for m in METRICS}


# ----- parsing the YAML plan -----


def _counts(raw: object, where: str) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"{where} must be a mapping")
    values = cast("dict[str, object]", raw)
    unknown = sorted(set(values) - METRIC_KEYS)
    if unknown:
        raise ValueError(f"{where} has unknown metrics: {', '.join(unknown)}")
    return {key: max(0, int(cast("int", value))) for key, value in values.items()}


def _text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    return str(value).strip() or None if value is not None else None


def _task(raw: object, total: int) -> PlanTask:
    if not isinstance(raw, dict):
        raise TypeError("each task must be a mapping")
    item = cast("dict[str, object]", raw)
    metric = _text(item, "metric")
    if metric is not None and metric not in METRIC_KEYS:
        raise ValueError(f"task metric {metric} is unknown")
    links = tuple(
        (str(link["label"]), str(link["url"]))
        for link in cast("list[dict[str, object]]", item.get("links") or [])
    )
    return PlanTask(
        id=str(item["id"]),
        title=str(item["title"]),
        weeks=task_weeks(item.get("weeks"), total),
        when=_text(item, "when"),
        steps=_text(item, "steps"),
        links=links,
        metric=metric,
    )


def parse_plan(raw: Mapping[str, object]) -> Plan:
    """Raises `ValueError`, `TypeError` or `KeyError` on a malformed plan."""
    weeks = int(cast("int", raw.get("weeks") or 6))
    if not 1 <= weeks <= MAX_WEEKS:
        raise ValueError("weeks must be between 1 and 52")
    start = raw.get("start")
    if start is not None and not isinstance(start, date):
        start = date.fromisoformat(str(start))
    week_targets = {
        int(cast("int", week)): _counts(values, f"week_targets.{week}")
        for week, values in cast("dict[object, object]", raw.get("week_targets") or {}).items()
    }
    checkpoints = tuple(
        Checkpoint(week=int(cast("int", c["week"])), text=str(c["text"]).strip())
        for c in cast("list[dict[str, object]]", raw.get("checkpoints") or [])
    )
    tasks = tuple(_task(t, weeks) for t in cast("list[object]", raw.get("tasks") or []))
    return Plan(
        name=str(raw.get("name") or "Campaign"),
        weeks=weeks,
        start=start,
        start_offset_days=int(cast("int", raw.get("start_offset_days") or 0)),
        targets=_counts(raw.get("targets"), "targets"),
        week_targets=week_targets,
        checkpoints=tuple(sorted(checkpoints, key=lambda c: c.week)),
        tasks=tasks,
    )


# ----- views -----


@dataclass(frozen=True, kw_only=True)
class MetricView:
    key: str
    label: str
    actual: int
    target: int


@dataclass(frozen=True, kw_only=True)
class CheckpointView:
    week: int
    text: str
    due: date


@dataclass(frozen=True, kw_only=True)
class PlanView:
    name: str
    start: date
    end: date
    total_weeks: int
    current_week: int
    week: int
    week_start: date
    week_end: date
    metrics: tuple[MetricView, ...]
    checkpoints: tuple[CheckpointView, ...]
    checkpoint: CheckpointView | None = None


@dataclass(frozen=True, kw_only=True)
class ProgressView:
    metric: str
    actual: int
    target: int


@dataclass(frozen=True, kw_only=True)
class TaskView:
    id: str
    title: str
    links: tuple[tuple[str, str], ...]
    done: bool
    when: str | None = None
    steps: str | None = None
    progress: ProgressView | None = None


@dataclass(frozen=True, kw_only=True)
class TaskListView:
    week: int
    items: tuple[TaskView, ...]


@dataclass(frozen=True, kw_only=True)
class ActivityListView:
    week: int
    items: tuple[ActivityRecord, ...]


@dataclass(frozen=True, kw_only=True)
class ActivityDraft:
    on_date: date
    kind: str
    who: str | None = None
    company: str | None = None
    role_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(frozen=True)
class _Frame:
    plan: Plan
    start: date
    current: int


class CampaignService:
    def __init__(
        self,
        source: CampaignPlanSource,
        store: CampaignStore,
        roles: RoleService,
        clock: Clock,
        ids: Ids,
        prefs: PrefsSource | None = None,
    ) -> None:
        self._prefs = prefs
        self._source = source
        self._store = store
        self._roles = roles
        self._clock = clock
        self._ids = ids

    async def plan(self, principal: Principal, week: int | None) -> PlanView:
        frame = await self._frame(principal)
        plan, start = frame.plan, frame.start
        selected = clamp_week(week, frame.current, plan.weeks)
        actuals = await self._actuals(principal, start, selected)
        targets = targets_for(plan, selected)
        first, last = week_bounds(start, selected)
        checkpoints = tuple(
            CheckpointView(week=c.week, text=c.text, due=week_bounds(start, c.week)[1])
            for c in plan.checkpoints
        )
        upcoming = [c for c in checkpoints if c.week >= selected]
        return PlanView(
            name=plan.name,
            start=start,
            end=week_bounds(start, plan.weeks)[1],
            total_weeks=plan.weeks,
            current_week=frame.current,
            week=selected,
            week_start=first,
            week_end=last,
            metrics=tuple(
                MetricView(
                    key=m.key, label=m.label, actual=actuals[m.key], target=targets.get(m.key, 0)
                )
                for m in METRICS
            ),
            checkpoints=checkpoints,
            checkpoint=upcoming[0] if upcoming else None,
        )

    async def tasks(self, principal: Principal, week: int | None) -> TaskListView:
        frame = await self._frame(principal)
        selected = clamp_week(week, frame.current, frame.plan.weeks)
        tasks = [t for t in frame.plan.tasks if selected in t.weeks]
        done = await self._store.done_tasks(principal.user_id, selected)
        actuals: dict[str, int] = {}
        if any(t.metric for t in tasks):
            actuals = await self._actuals(principal, frame.start, selected)
        targets = targets_for(frame.plan, selected)
        items = tuple(
            TaskView(
                id=t.id,
                title=t.title,
                when=t.when,
                steps=t.steps,
                links=t.links,
                done=t.id in done,
                progress=ProgressView(
                    metric=t.metric, actual=actuals[t.metric], target=targets.get(t.metric, 0)
                )
                if t.metric
                else None,
            )
            for t in tasks
        )
        return TaskListView(week=selected, items=items)

    async def set_task_done(
        self, principal: Principal, task_id: str, week: int, *, done: bool
    ) -> None:
        frame = await self._frame(principal)
        if not any(t.id == task_id and week in t.weeks for t in frame.plan.tasks):
            raise NotFound("That task isn't in the plan for that week.")
        if done:
            await self._store.set_task_done(principal.user_id, task_id, week, self._clock.now())
        else:
            await self._store.clear_task_done(principal.user_id, task_id, week)

    async def activity(self, principal: Principal, week: int | None) -> ActivityListView:
        frame = await self._frame(principal)
        selected = clamp_week(week, frame.current, frame.plan.weeks)
        first, last = week_bounds(frame.start, selected)
        rows = await self._store.list_activity(principal.user_id, first, last + timedelta(days=1))
        return ActivityListView(week=selected, items=tuple(rows))

    async def log_activity(self, principal: Principal, draft: ActivityDraft) -> ActivityRecord:
        if draft.kind not in KINDS:
            raise ValidationFailed(
                "Pick what you did.", errors=[FieldError("/kind", "Unknown activity.")]
            )
        today = self._clock.now().astimezone(UTC).date()
        if draft.on_date > today + timedelta(days=1):
            raise ValidationFailed(
                "Activity can't be logged in the future.",
                errors=[FieldError("/on_date", "Pick today or an earlier day.")],
            )
        if draft.role_id is not None:
            try:
                await self._roles.get_role(principal, draft.role_id)
            except NotFound:
                raise ValidationFailed(
                    "That role doesn't exist.",
                    errors=[FieldError("/role_id", "Unknown role.")],
                ) from None
        values: dict[str, object] = {
            "id": self._ids.new_id(),
            "on_date": draft.on_date,
            "kind": draft.kind,
            "who": _clean(draft.who),
            "company": _clean(draft.company),
            "role_id": draft.role_id,
            "notes": _clean(draft.notes),
            "created_at": self._clock.now(),
        }
        return await self._store.add_activity(principal.user_id, values)

    async def delete_activity(self, principal: Principal, activity_id: uuid.UUID) -> None:
        if not await self._store.delete_activity(principal.user_id, activity_id):
            raise NotFound("That activity doesn't exist.")

    async def _frame(self, principal: Principal) -> _Frame:
        plan = parse_plan(self._source.load())
        today = self._clock.now().astimezone(UTC).date()
        override = (await prefs_for(self._prefs, principal.user_id)).campaign_start
        if override is not None:
            # The user's `campaign.start_date` setting replaces the plan's own start.
            plan = replace(plan, start=override)
        anchor = today
        if plan.start is None:
            note = await self._store.note(principal.user_id, SEED_ANCHOR_NOTE)
            anchor = _date_or(note, today)
        start = plan_start(plan, anchor)
        return _Frame(plan=plan, start=start, current=week_index(today, start))

    async def _actuals(self, principal: Principal, start: date, week: int) -> dict[str, int]:
        first, last = week_bounds(start, week)
        facts = await self._store.facts(principal.user_id, first, last + timedelta(days=1))
        return count_actuals(facts)


def _clean(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def _date_or(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return fallback
