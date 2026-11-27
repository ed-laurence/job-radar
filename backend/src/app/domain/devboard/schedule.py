"""Scheduled jobs (`python -m app.jobs.<name>`): the run record, daily spend caps, and the
wrapper every job runs inside. Each job has its own daily cap on top of the per-feature LLM
budgets in `ai/models.yaml`; a job whose cap is used up skips its AI work and says so."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.domain.errors import DomainError
from app.domain.ports import Clock, LlmFeature, LlmUsageStore


class JobName(StrEnum):
    SCAN = "scan"
    FEED_REPLAY = "scan"  # the old name: an alias of SCAN
    DIGEST = "digest"
    ROLLUP = "rollup"
    WATCHDOG = "watchdog"


@dataclass(frozen=True)
class SpendCap:
    features: tuple[LlmFeature, ...]
    daily_usd: float


JOB_CAPS: dict[JobName, SpendCap] = {
    JobName.SCAN: SpendCap((LlmFeature.SCORE,), 0.50),
    JobName.DIGEST: SpendCap((LlmFeature.DIGEST,), 0.20),
    JobName.ROLLUP: SpendCap((), 0.0),
    JobName.WATCHDOG: SpendCap((), 0.0),
}


@dataclass
class JobReport:
    job: JobName
    ok: bool = True
    items_seen: int = 0
    items_new: int = 0
    error_code: str | None = None
    notes: list[str] = field(default_factory=list[str])

    def note(self, text: str) -> None:
        self.notes.append(text)

    def fail(self, code: str, text: str) -> None:
        self.ok = False
        self.error_code = code
        self.notes.append(text)


class JobRunLog(Protocol):
    """`job_runs` rows: one per run, finished with its counts and error code."""

    async def start(self, job: JobName, at: datetime) -> uuid.UUID: ...

    async def finish(self, run_id: uuid.UUID, report: JobReport, at: datetime) -> None: ...


class Budget:
    """The job's daily cap, read from the same spend ledger the LLM adapter writes."""

    def __init__(self, cap: SpendCap, usage: LlmUsageStore, clock: Clock) -> None:
        self._cap = cap
        self._usage = usage
        self._clock = clock

    async def spent(self) -> float:
        day = self._clock.now().date()
        total = 0.0
        for feature in self._cap.features:
            total += await self._usage.spent(day, feature)
        return total

    async def has_room(self) -> bool:
        if not self._cap.features:
            return False
        return await self.spent() < self._cap.daily_usd


async def run_job(
    job: JobName,
    log: JobRunLog,
    clock: Clock,
    body: Callable[[JobReport], Awaitable[None]],
) -> JobReport:
    report = JobReport(job=job)
    run_id = await log.start(job, clock.now())
    try:
        await body(report)
    except DomainError as error:
        report.fail(error.code, error.detail or "The job failed.")
    await log.finish(run_id, report, clock.now())
    return report
