"""The in-process PR sync loop: a pass every interval, one log line per distinct failure, and
it outlives any failed pass until it is cancelled."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from structlog.testing import capture_logs

from app.domain.devboard.model import Actor, ActorKind
from app.domain.devboard.pr_sync import PrSyncSummary
from app.domain.identity import AppRole
from app.jobs.pr_sync import run_pr_sync

SYSTEM = Actor(uuid.UUID(int=1), ActorKind.SYSTEM, AppRole.MAINTAINER)
OK = PrSyncSummary(checked=1)


class Stop(Exception):
    """Ends the loop from the fake sleep once the scripted passes ran."""


class ScriptedSync:
    """Each pass returns (or raises) the next scripted outcome."""

    def __init__(self, outcomes: list[PrSyncSummary | Exception]) -> None:
        self._outcomes = outcomes
        self.actors: list[Actor] = []
        self.ran = asyncio.Event()

    async def sync(self, actor: Actor) -> PrSyncSummary:
        self.actors.append(actor)
        self.ran.set()
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Sleeps:
    def __init__(self, passes: int, service: ScriptedSync) -> None:
        self.durations: list[float] = []
        self._passes = passes
        self._service = service

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        if len(self._service.actors) >= self._passes:
            raise Stop


async def run_scripted(outcomes: list[PrSyncSummary | Exception]) -> tuple[ScriptedSync, Sleeps]:
    service = ScriptedSync(list(outcomes))
    sleep = Sleeps(len(outcomes), service)
    with pytest.raises(Stop):
        await run_pr_sync(service, 60, actor=SYSTEM, sleep=sleep)
    return service, sleep


async def test_a_pass_runs_then_waits_the_interval() -> None:
    service, sleep = await run_scripted([OK, OK, OK])

    assert service.actors == [SYSTEM, SYSTEM, SYSTEM]
    assert sleep.durations == [60, 60, 60]


async def test_each_distinct_failure_is_logged_once() -> None:
    signed_out = PrSyncSummary(failure="gh is not signed in")
    missing = PrSyncSummary(failure="gh is not installed")

    with capture_logs() as logs:
        await run_scripted([signed_out, signed_out, missing, signed_out, missing, OK])

    failures = [entry for entry in logs if entry["event"] == "pr_sync_unavailable"]
    assert [entry["reason"] for entry in failures] == ["gh is not signed in", "gh is not installed"]


async def test_the_loop_survives_a_failing_pass_and_logs_each_error_kind_once() -> None:
    with capture_logs() as logs:
        service, _ = await run_scripted(
            [RuntimeError("boom"), RuntimeError("again"), ValueError("bad"), OK]
        )

    assert len(service.actors) == 4
    failures = [entry for entry in logs if entry["event"] == "pr_sync_failed"]
    assert [entry["error"] for entry in failures] == ["RuntimeError", "ValueError"]


async def test_cancelling_the_loop_stops_it() -> None:
    service = ScriptedSync([OK] * 1000)
    task = asyncio.create_task(run_pr_sync(service, 0.001, actor=SYSTEM))
    await service.ran.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_a_non_positive_interval_is_refused() -> None:
    service = ScriptedSync([OK])

    with pytest.raises(ValueError, match="interval"):
        await run_pr_sync(service, 0, actor=SYSTEM)
    assert service.actors == []
