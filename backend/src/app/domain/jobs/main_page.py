"""Main-page extras: alerts and the Feedback tab's rejection list.

Alerts are user-facing sentences; they never carry error codes, ids or other internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from app.domain.health import SCHEDULES
from app.domain.identity import Principal
from app.domain.jobs.pagination import decode_cursor, to_page
from app.domain.jobs.ports import CampaignStore
from app.domain.jobs.records import Page, RejectionSummaryRecord
from app.domain.ports import Clock
from app.domain.ports.health import HealthSource
from app.domain.user_settings import PrefsSource, prefs_for

QUIET_FEED = timedelta(days=2)
# `job_runs.error_code` of an ok scan that found nothing (an empty pool is not a failure).
NO_NEW_ROLES = "no-new-roles"
REJECTION_WINDOW = 500
JOB_LABELS = {
    "scan": "role scan",
    "digest": "daily digest",
    "rollup": "nightly statistics",
    "watchdog": "board watchdog",
}

type AlertLevel = Literal["info", "warning", "error"]
type ReasonStatus = Literal["missing", "pasted", "analysed"]


@dataclass(frozen=True, kw_only=True)
class AlertView:
    level: AlertLevel
    title: str
    text: str


@dataclass(frozen=True, kw_only=True)
class RejectionSummaryView:
    record: RejectionSummaryRecord
    reason_status: ReasonStatus


def _span(hours: int) -> str:
    """`24` -> "a day", `48` -> "2 days", `36` -> "36 hours"."""
    if hours == 24:
        return "a day"
    if hours % 24 == 0:
        return f"{hours // 24} days"
    return "an hour" if hours == 1 else f"{hours} hours"


def reason_status(record: RejectionSummaryRecord) -> ReasonStatus:
    if record.summary is not None or record.categories:
        return "analysed"
    return "pasted" if record.has_text else "missing"


class MainPageService:
    def __init__(
        self,
        health: HealthSource,
        store: CampaignStore,
        clock: Clock,
        prefs: PrefsSource | None = None,
    ) -> None:
        self._prefs = prefs
        self._health = health
        self._store = store
        self._clock = clock

    async def alerts(self, principal: Principal) -> list[AlertView]:
        now = self._clock.now()
        alerts: list[AlertView] = []
        runs = await self._health.last_job_runs(tuple(job.value for job in SCHEDULES))
        feed_ok = pool_empty = False
        for run in sorted(runs, key=lambda r: r.job):
            label = JOB_LABELS.get(run.job, "scheduled job")
            if run.job == "scan" and run.ok:
                feed_ok = now - run.started_at <= QUIET_FEED
                pool_empty = run.error_code == NO_NEW_ROLES
            if not run.ok:
                alerts.append(
                    AlertView(
                        level="error",
                        title=f"The {label} failed",
                        text=f"Its last run on {run.started_at:%d %b} didn't finish. "
                        "What it shows may be out of date until the next run.",
                    )
                )
        followups = (await prefs_for(self._prefs, principal.user_id)).followups
        facts = await self._store.main_page(principal.user_id, now - followups.after)
        if facts.followups_due:
            plural = "s" if facts.followups_due != 1 else ""
            alerts.append(
                AlertView(
                    level="warning",
                    title=f"{facts.followups_due} application{plural} to follow up",
                    text=f"You applied more than {_span(followups.after_hours)} ago and haven't "
                    "followed up yet.",
                )
            )
        if pool_empty:
            alerts.append(
                AlertView(
                    level="info",
                    title="No new roles",
                    text="The last scan found no new roles: every held-back role has been "
                    "released.",
                )
            )
        latest = facts.latest_feed_role_at
        if feed_ok and (latest is None or now - latest > QUIET_FEED):
            alerts.append(
                AlertView(
                    level="info",
                    title="No new roles from the feed",
                    text="The role feed ran but found no new roles in the last two days. "
                    "Check your saved searches.",
                )
            )
        return alerts

    async def rejections(
        self,
        principal: Principal,
        status: ReasonStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[RejectionSummaryView]:
        offset = decode_cursor(cursor)
        rows = await self._store.rejection_summaries(principal.user_id, 0, REJECTION_WINDOW)
        views = [RejectionSummaryView(record=row, reason_status=reason_status(row)) for row in rows]
        if status is not None:
            views = [view for view in views if view.reason_status == status]
        return to_page(views[offset : offset + limit + 1], offset, limit)
