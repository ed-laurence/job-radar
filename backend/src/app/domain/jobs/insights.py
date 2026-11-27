"""Job-search insights (spec `getJobInsights`), computed from raw facts like the rest of the
dashboard so the in-memory fake and Postgres agree and a SQL ground truth can check each number.

Definitions (UTC; "applied" = live roles with `applied_at` in the range):
- reached statuses of a role: every `status` event's `to_value` (any time) plus its status now.
- response / interview / offer rate: share of applied roles that reached `interview`, `offer`
  or `rejected` / `interview` or `offer` / `offer`. Ghost rate: applied roles now `unknown`.
- applications per week: applied roles by the Monday of `applied_at` (the latest 60 weeks).
- median days to response: from `applied_at` to the first later `status` event to `interview`,
  `offer` or `rejected` (`percentile_cont(0.5)`).
- follow-up on time: of applied roles that were followed up or whose follow-up is past due
  (`applied_at + followups.due_hours <= now`), the share followed up by the due time.
- breakdowns (by source, by track, by score band): live roles found in the range; `applied` =
  has `applied_at`; `interviews` / `rejected` = reached `interview`/`offer` / `rejected`. Score
  bands split at the user's borderline, keep and notify scores; `unscored` collects the rest.
- rates are rounded to 4 places, days to 2; every rate is null when there is nothing to divide.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.domain.jobs.metrics import DateRange, bucket_start, percentile, rounded
from app.domain.jobs.ports import RoleFact, StatusFact
from app.domain.user_settings import UserPrefs

RESPONSE = frozenset({"interview", "offer", "rejected"})
INTERVIEW = frozenset({"interview", "offer"})
MAX_WEEKS = 60
MAX_KEYS = 20
MAX_KEY_CHARS = 40
UNSCORED = "unscored"
UNKNOWN_TRACK = "unknown"
DAY_SECONDS = 86_400


@dataclass(frozen=True, kw_only=True)
class WeekCount:
    week_start: date
    applied: int


@dataclass(frozen=True, kw_only=True)
class Breakdown:
    key: str
    found: int
    applied: int
    interviews: int
    rejected: int


@dataclass(frozen=True, kw_only=True)
class InsightsView:
    applications_per_week: tuple[WeekCount, ...]
    by_source: tuple[Breakdown, ...]
    by_track: tuple[Breakdown, ...]
    score_bands: tuple[Breakdown, ...]
    as_of: datetime
    response_rate: float | None = None
    interview_rate: float | None = None
    offer_rate: float | None = None
    ghost_rate: float | None = None
    median_days_to_response: float | None = None
    followup_on_time_rate: float | None = None


def _rate(part: int, whole: int) -> float | None:
    return rounded(part / whole, 4) if whole else None


def score_bands(prefs: UserPrefs) -> list[tuple[int, int]]:
    """`(low, high)` bands split at the borderline, keep and notify scores."""
    scoring = prefs.scoring
    cuts = sorted(
        {c for c in (scoring.borderline_score, scoring.keep_score, scoring.notify_score) if c > 0}
    )
    lows = [0, *cuts]
    return [(low, (lows[i + 1] - 1) if i + 1 < len(lows) else 100) for i, low in enumerate(lows)]


def _band_key(score: int | None, bands: Sequence[tuple[int, int]]) -> str:
    if score is None:
        return UNSCORED
    for low, high in bands:
        if low <= score <= high:
            return f"{low}-{high}"
    return f"{bands[-1][0]}-{bands[-1][1]}"


def _breakdown(
    roles: Sequence[RoleFact],
    reached: dict[uuid.UUID, set[str]],
    key_of: Callable[[RoleFact], str],
) -> dict[str, Breakdown]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for role in roles:
        row = counts[key_of(role)[:MAX_KEY_CHARS]]
        statuses = reached[role.id]
        row[0] += 1
        row[1] += int(role.applied_at is not None)
        row[2] += int(bool(statuses & INTERVIEW))
        row[3] += int("rejected" in statuses)
    return {
        key: Breakdown(key=key, found=f, applied=a, interviews=i, rejected=r)
        for key, (f, a, i, r) in counts.items()
    }


def _ranked(rows: dict[str, Breakdown]) -> tuple[Breakdown, ...]:
    return tuple(sorted(rows.values(), key=lambda row: (-row.found, row.key))[:MAX_KEYS])


def _days_to_response(role: RoleFact, events: Sequence[StatusFact]) -> float | None:
    applied_at = role.applied_at
    if applied_at is None:
        return None
    replies = [e.at for e in events if e.to_value in RESPONSE and e.at >= applied_at]
    return (min(replies) - applied_at).total_seconds() / DAY_SECONDS if replies else None


def _on_time(applied: Sequence[RoleFact], due: timedelta, now: datetime) -> float | None:
    counted = on_time = 0
    for role in applied:
        if role.applied_at is None:
            continue
        deadline = role.applied_at + due
        if role.followed_up_at is None and deadline > now:
            continue
        counted += 1
        on_time += int(role.followed_up_at is not None and role.followed_up_at <= deadline)
    return _rate(on_time, counted)


def job_insights(
    roles: Sequence[RoleFact],
    events: Sequence[StatusFact],
    window: DateRange,
    prefs: UserPrefs,
    now: datetime,
) -> InsightsView:
    by_role: dict[uuid.UUID, list[StatusFact]] = defaultdict(list)
    for event in events:
        by_role[event.role_id].append(event)
    reached = {role.id: {e.to_value for e in by_role[role.id]} | {role.status} for role in roles}
    applied = [role for role in roles if window.contains(role.applied_at)]
    weeks = Counter(
        bucket_start(role.applied_at, "week") for role in applied if role.applied_at is not None
    )
    days = [d for d in (_days_to_response(r, by_role[r.id]) for r in applied) if d is not None]
    found = [role for role in roles if window.contains(role.found_at)]
    bands = score_bands(prefs)
    band_rows = _breakdown(found, reached, lambda role: _band_key(role.score, bands))
    band_order = [f"{low}-{high}" for low, high in bands]
    if UNSCORED in band_rows:
        band_order.append(UNSCORED)
    empty = {"found": 0, "applied": 0, "interviews": 0, "rejected": 0}
    return InsightsView(
        applications_per_week=tuple(
            WeekCount(week_start=week, applied=count) for week, count in sorted(weeks.items())
        )[-MAX_WEEKS:],
        response_rate=_rate(sum(1 for r in applied if reached[r.id] & RESPONSE), len(applied)),
        interview_rate=_rate(sum(1 for r in applied if reached[r.id] & INTERVIEW), len(applied)),
        offer_rate=_rate(sum(1 for r in applied if "offer" in reached[r.id]), len(applied)),
        ghost_rate=_rate(sum(1 for r in applied if r.status == "unknown"), len(applied)),
        median_days_to_response=rounded(percentile(days, 0.5)),
        followup_on_time_rate=_on_time(applied, prefs.followups.due, now),
        by_source=_ranked(_breakdown(found, reached, lambda role: role.source)),
        by_track=_ranked(_breakdown(found, reached, lambda role: role.track or UNKNOWN_TRACK)),
        score_bands=tuple(band_rows.get(key) or Breakdown(key=key, **empty) for key in band_order),
        as_of=now,
    )


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
