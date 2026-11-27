"""The scan (spec `runScan`, `getScanStatus`): fetch new roles from the job source for every
account, dedupe them on source + external id, save them as new roles, then score each one
through the jobs area's scorer, which puts it in Act now or Filtered by the user's threshold.

- A role is saved before it is scored, so a scoring failure (or the daily spend cap) never
  loses it: it stays new and unscored, counted under `failed`.
- An empty source is not an error: the run finishes `no_new_roles`.
- Single flight: a scan runs only while it holds the scan lock; a second one gets 409
  `scan-running` (manual) or is skipped (scheduled).
- `ScanScheduler` runs the scan every `interval` minutes inside the API process (local dev);
  deployments use Cloud Scheduler instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.devboard.schedule import Budget, JobName, JobReport, JobRunLog
from app.domain.errors import Conflict, DomainError
from app.domain.jobs.main_page import NO_NEW_ROLES
from app.domain.jobs.ports import NewRoleEvent, RoleScorer, RoleStore
from app.domain.jobs.ranking import key_between
from app.domain.ports import Clock, Ids
from app.domain.ports.scan import (
    JobSource,
    KnownRoles,
    ScanAccounts,
    ScanLock,
    ScanRunRecord,
    ScanRunStore,
    ScrapedRole,
)

log = logging.getLogger(__name__)

MANUAL = "manual"
SCHEDULE = "schedule"


class ScanRunning(Conflict):
    code = "scan-running"


@dataclass(frozen=True, kw_only=True)
class ScanStatusView:
    last_run: ScanRunRecord | None
    next_run_at: datetime | None
    interval_minutes: int | None
    pool_remaining: int
    running: bool


@dataclass
class _Tally:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    scored: int = 0
    failed: int = 0


@dataclass(kw_only=True)
class ScanService:
    source: JobSource
    roles: RoleStore
    known: KnownRoles
    accounts: ScanAccounts
    runs: ScanRunStore
    lock: ScanLock
    clock: Clock
    ids: Ids
    scorer: RoleScorer | None = None
    budget: Budget | None = None
    interval_minutes: int | None = None
    job_log: JobRunLog | None = None

    async def run(self, trigger: str = MANUAL) -> ScanRunRecord:
        """Raises `ScanRunning` when another scan holds the lock."""
        async with self.lock.hold() as held:
            if not held:
                raise ScanRunning("A scan is already running.")
            log_id = None
            if self.job_log is not None:
                log_id = await self.job_log.start(JobName.SCAN, self.clock.now())
            run = await self._run_locked(trigger)
            if self.job_log is not None and log_id is not None:
                await self.job_log.finish(log_id, _job_report(run), self.clock.now())
            return run

    async def status(self) -> ScanStatusView:
        last = await self.runs.last()
        next_run = None
        if self.interval_minutes is not None:
            base = last.started_at if last is not None else self.clock.now()
            next_run = max(base + timedelta(minutes=self.interval_minutes), self.clock.now())
        return ScanStatusView(
            last_run=last,
            next_run_at=next_run,
            interval_minutes=self.interval_minutes,
            pool_remaining=await self.source.remaining(),
            running=await self.lock.is_held(),
        )

    async def _run_locked(self, trigger: str) -> ScanRunRecord:
        previous = await self.runs.last()
        since = previous.started_at if previous is not None else None
        run = ScanRunRecord(
            id=self.ids.new_id(),
            started_at=self.clock.now(),
            trigger=trigger,
            source=self.source.name,
            status="running",
        )
        await self.runs.save(run)
        tally = _Tally()
        notes: list[str] = []
        try:
            for user_id in await self.accounts.active_users():
                await self._scan_user(user_id, since, tally, notes)
        except DomainError as error:
            notes.append(f"Scan stopped: {error.code}.")
            return await self._finish(run, tally, "failed", notes)
        if tally.fetched == 0:
            notes.append("No new roles: the source had nothing new.")
            return await self._finish(run, tally, "no_new_roles", notes)
        return await self._finish(run, tally, "ok", notes)

    async def _finish(
        self, run: ScanRunRecord, tally: _Tally, status: str, notes: list[str]
    ) -> ScanRunRecord:
        done = dataclasses.replace(
            run,
            status=status,
            finished_at=self.clock.now(),
            pool_remaining=await self.source.remaining(),
            note=" ".join(dict.fromkeys(notes))[:500] or None,
            **dataclasses.asdict(tally),
        )
        await self.runs.save(done)
        return done

    async def _scan_user(
        self, user_id: uuid.UUID, since: datetime | None, tally: _Tally, notes: list[str]
    ) -> None:
        scraped = await self.source.fetch(user_id, since)
        tally.fetched += len(scraped)
        ids = tuple(role.external_id for role in scraped)
        seen: set[str] = await self.known.known(user_id, self.source.name, ids) if ids else set()
        for role in scraped:
            if role.external_id in seen:
                tally.duplicates += 1
                continue
            seen.add(role.external_id)
            role_id = await self._save(user_id, role)
            tally.new += 1
            if await self._score(user_id, role_id, notes):
                tally.scored += 1
            else:
                tally.failed += 1

    async def _save(self, user_id: uuid.UUID, role: ScrapedRole) -> uuid.UUID:
        now = self.clock.now()
        ranks = await self.roles.column_ranks(user_id, "new")
        first = min((rank for _, rank in ranks), default=None)
        role_id = self.ids.new_id()
        values: dict[str, object] = {
            "id": role_id,
            "source": role.source,
            "external_id": role.external_id,
            "title": role.title,
            "url": role.url,
            "company": role.company,
            "location": role.location,
            "work_mode": role.work_mode,
            "pay": role.pay,
            "posted_at": role.posted_at,
            "found_at": role.found_at,
            "description": role.description,
            "status": "new",
            "stage": "new",
            "rank": key_between(None, first) or "m",
            "raw": dict(role.raw) if role.raw is not None else None,
            "created_at": now,
            "updated_at": now,
        }
        event = NewRoleEvent(id=self.ids.new_id(), at=now, kind="status", to_value="new")
        record = await self.roles.insert_role(user_id, values, (event,))
        return record.id

    async def _score(self, user_id: uuid.UUID, role_id: uuid.UUID, notes: list[str]) -> bool:
        if self.scorer is None:
            notes.append("No scoring service: roles saved unscored.")
            return False
        if self.budget is not None and not await self.budget.has_room():
            notes.append("Daily spend cap reached: the rest stay unscored.")
            return False
        try:
            await self.scorer.score_role(user_id, role_id)
        except DomainError as error:
            notes.append(f"Scoring failed for some roles: {error.code}.")
            return False
        except Exception:  # any scoring failure leaves the saved role unscored, never lost
            log.exception("scan scoring failed")
            notes.append("Scoring failed for some roles.")
            return False
        return True


def _job_report(run: ScanRunRecord) -> JobReport:
    report = JobReport(job=JobName.SCAN, items_seen=run.fetched, items_new=run.new)
    if run.status == "failed":
        report.fail("scan-failed", run.note or "The scan failed.")
    elif run.status == "no_new_roles":
        report.error_code = NO_NEW_ROLES
    return report


class ScanScheduler:
    """Runs the scan every `interval_minutes` in a background task (local only)."""

    def __init__(self, service: ScanService, interval_minutes: int) -> None:
        self._service = service
        self._interval = interval_minutes * 60
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scan-scheduler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def tick(self) -> ScanRunRecord | None:
        """One scheduled run; None when another scan already holds the lock."""
        try:
            return await self._service.run(SCHEDULE)
        except ScanRunning:
            return None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.tick()
            except Exception:  # the loop must survive a failed run; the run record has it
                log.exception("scheduled scan failed")
