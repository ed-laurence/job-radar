"""Main-page summary (spec `getJobSummary`): KPI tiles and status-tab counts.

Definitions:
- filtered: a role the screening stage filtered out (`stage = 'filtered'`) that the user has not
  moved on from `new` or `reviewing`. Once moved, it counts in its new status's tab.
- act now: `new` or `reviewing`, not filtered. `to_act_on` is its count.
- oldest waiting: hours since the oldest act-now role was posted (`found_at` when not posted).
- applied in the last 7 days: `applied_at` in `[now - 7 days, now]`.
- median post-to-apply: over every applied role with `posted_at <= applied_at`.
- follow-ups due: `applied` roles with no `followed_up_at`, applied at least the user's
  `followups.after_hours` ago (default 24).
- outreach / feedback tabs: contacts needing outreach and rejections without analysed reasons
  (counted by the store, passed in as `MainPageFacts`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.jobs.metrics import hours_between, percentile, rounded
from app.domain.jobs.ports import MainPageFacts, RoleFact
from app.domain.user_settings import DEFAULTS

ACT_NOW_STATUSES = frozenset({"new", "reviewing"})
TAB_OF_STATUS = {
    "new": "act_now",
    "reviewing": "act_now",
    "applied": "applied",
    "interview": "interviewing",
    "offer": "interviewing",
    "passed": "passed",
    "closed": "passed",
    "rejected": "rejected",
    "unknown": "unknown",
}
TABS = ("act_now", "applied", "interviewing", "passed", "rejected", "unknown", "filtered")
RECENT = timedelta(days=7)
FOLLOWUP_AFTER = DEFAULTS.followups.after


@dataclass(frozen=True, kw_only=True)
class JobSummaryView:
    to_act_on: int
    oldest_waiting_hours: float | None
    applied_last_7_days: int
    median_post_to_apply_hours: float | None
    followups_due: int
    tab_counts: dict[str, int]
    as_of: datetime


def tab_of(role: RoleFact) -> str | None:
    if role.status in ACT_NOW_STATUSES and role.stage == "filtered":
        return "filtered"
    return TAB_OF_STATUS.get(role.status)


def followups_due(
    roles: Sequence[RoleFact], now: datetime, after: timedelta = FOLLOWUP_AFTER
) -> int:
    return sum(
        1
        for role in roles
        if role.status == "applied"
        and role.followed_up_at is None
        and role.applied_at is not None
        and role.applied_at <= now - after
    )


def job_summary(
    roles: Sequence[RoleFact],
    now: datetime,
    extras: MainPageFacts | None = None,
    followup_after: timedelta = FOLLOWUP_AFTER,
) -> JobSummaryView:
    tabs = Counter(tab for tab in map(tab_of, roles) if tab is not None)
    act_now = [role for role in roles if tab_of(role) == "act_now"]
    waiting = [hours_between(role.posted_at or role.found_at, now) for role in act_now]
    applied_hours = [
        hours_between(role.posted_at, role.applied_at)
        for role in roles
        if role.posted_at is not None
        and role.applied_at is not None
        and role.applied_at >= role.posted_at
    ]
    recent = sum(
        1
        for role in roles
        if role.applied_at is not None and now - RECENT <= role.applied_at <= now
    )
    return JobSummaryView(
        to_act_on=len(act_now),
        oldest_waiting_hours=rounded(max(0.0, *waiting)) if waiting else None,
        applied_last_7_days=recent,
        median_post_to_apply_hours=rounded(percentile(applied_hours, 0.5)),
        followups_due=followups_due(roles, now, followup_after),
        tab_counts={
            **{tab: tabs[tab] for tab in TABS},
            "outreach": extras.outreach_due if extras else 0,
            "feedback": extras.feedback_due if extras else 0,
        },
        as_of=now,
    )
