"""The scan pipeline on in-memory adapters: pool release order and idempotency, dedupe, scoring
failures that keep the role, the spend cap, single flight and the local scheduler."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.fakes.data import InMemoryUsageStore
from app.adapters.fakes.jobs import MemoryRoles
from app.adapters.fakes.scan import (
    MemoryKnownRoles,
    MemoryScanAccounts,
    MemoryScanLock,
    MemoryScanRuns,
)
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.adapters.scrapers.seed_feed import (
    MAX_POSTED_AGO_MINUTES,
    MemorySeedFeed,
    PoolEntry,
    posted_offset,
)
from app.domain.devboard.schedule import JOB_CAPS, Budget, JobName
from app.domain.errors import UpstreamUnavailable
from app.domain.jobs.ports import RoleQuery
from app.domain.jobs.scan import ScanRunning, ScanScheduler, ScanService
from app.domain.ports import LlmFeature

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
USER = uuid.UUID(int=11)


class Scorer:
    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.calls: list[uuid.UUID] = []
        self._fail_on = fail_on or set()

    async def score_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        assert user_id == USER
        self.calls.append(role_id)
        if len(self.calls) in self._fail_on:
            raise UpstreamUnavailable("model down")


def _pool(count: int) -> dict[uuid.UUID, list[PoolEntry]]:
    entries = [
        PoolEntry(
            external_id=f"ext-{n:03d}",
            posted_at=NOW - timedelta(days=n),
            payload={"title": f"Role {n}", "company": "Example Co", "description": "An ad."},
        )
        for n in range(count)
    ]
    return {USER: entries}


def _service(
    pool: int = 12, scorer: Scorer | None = None, roles: MemoryRoles | None = None
) -> tuple[ScanService, MemoryRoles, MemorySeedFeed, FakeClock]:
    clock = FakeClock(NOW)
    roles = roles or MemoryRoles()
    feed = MemorySeedFeed(clock, _pool(pool), batch=5)
    service = ScanService(
        source=feed,
        roles=roles,
        known=MemoryKnownRoles(roles),
        accounts=MemoryScanAccounts([USER]),
        runs=MemoryScanRuns(),
        lock=MemoryScanLock(),
        clock=clock,
        ids=SequentialIds(),
        scorer=scorer if scorer is not None else Scorer(),
        interval_minutes=15,
    )
    return service, roles, feed, clock


async def _external_ids(roles: MemoryRoles) -> list[str]:
    found = await roles.list_roles(USER, RoleQuery(sort="rank"), 0, 100)
    return sorted(str(role.external_id) for role in found)


async def test_each_scan_releases_the_next_five_oldest_posted_then_reports_an_empty_pool() -> None:
    service, roles, _, _ = _service(pool=12)
    first = await service.run()
    assert (first.status, first.fetched, first.new, first.scored) == ("ok", 5, 5, 5)
    assert first.pool_remaining == 7
    # Oldest posted first: ext-011 was posted 11 days ago.
    assert await _external_ids(roles) == [f"ext-{n:03d}" for n in range(7, 12)]
    second = await service.run()
    assert (second.new, second.pool_remaining) == (5, 2)
    third = await service.run()
    assert (third.new, third.pool_remaining) == (2, 0)
    empty = await service.run()
    assert empty.status == "no_new_roles"
    assert (empty.fetched, empty.new, empty.pool_remaining) == (0, 0, 0)
    assert empty.note is not None
    assert "No new roles" in empty.note
    assert len(await _external_ids(roles)) == 12


async def test_released_roles_are_found_now_and_posted_up_to_two_hours_ago() -> None:
    service, roles, _, _ = _service(pool=5)
    await service.run()
    for role in await roles.list_roles(USER, RoleQuery(), 0, 10):
        assert role.found_at == NOW
        assert role.posted_at is not None
        assert NOW - timedelta(minutes=MAX_POSTED_AGO_MINUTES) <= role.posted_at <= NOW
        assert role.posted_at == NOW - posted_offset(str(role.external_id))
        assert role.status == "new"
        assert role.source == "seed"
    assert posted_offset("ext-001") == posted_offset("ext-001")


async def test_a_role_already_saved_for_the_same_source_and_id_is_a_duplicate() -> None:
    roles = MemoryRoles()
    service, _, feed, _ = _service(pool=5, roles=roles)
    # The same posting arrives again: dedupe on (source, external id).
    again = [*feed.pool[USER][:2]]
    await service.run()
    feed.pool[USER] = again
    run = await service.run()
    assert (run.fetched, run.duplicates, run.new) == (2, 2, 0)
    assert len(await _external_ids(roles)) == 5


async def test_a_scoring_failure_keeps_the_role_unscored() -> None:
    scorer = Scorer(fail_on={2})
    service, roles, _, _ = _service(pool=5, scorer=scorer)
    run = await service.run()
    assert (run.new, run.scored, run.failed) == (5, 4, 1)
    assert run.status == "ok"
    assert len(await _external_ids(roles)) == 5
    assert run.note is not None
    assert "upstream-unavailable" in run.note


async def test_the_daily_spend_cap_leaves_roles_saved_but_unscored() -> None:
    service, roles, _, clock = _service(pool=5)
    usage = InMemoryUsageStore()
    cap = JOB_CAPS[JobName.SCAN]
    await usage.add_spend(clock.now().date(), LlmFeature.SCORE, cap.daily_usd)
    service.budget = Budget(cap, usage, clock)
    run = await service.run()
    assert (run.new, run.scored, run.failed) == (5, 0, 5)
    assert len(await _external_ids(roles)) == 5


async def test_only_one_scan_runs_at_a_time() -> None:
    service, _, _, _ = _service(pool=5)
    async with service.lock.hold() as held:
        assert held
        assert (await service.status()).running
        with pytest.raises(ScanRunning) as caught:
            await service.run()
        assert caught.value.code == "scan-running"
        # A scheduled tick that finds a scan running is skipped, not failed.
        assert await ScanScheduler(service, 15).tick() is None
    assert not (await service.status()).running


async def test_status_reports_the_last_run_next_run_and_pool() -> None:
    service, _, _, _ = _service(pool=7)
    before = await service.status()
    assert before.last_run is None
    assert (before.pool_remaining, before.interval_minutes) == (7, 15)
    run = await service.run()
    after = await service.status()
    assert after.last_run == run
    assert after.next_run_at == run.started_at + timedelta(minutes=15)
    assert after.pool_remaining == 2


async def test_the_scheduler_runs_on_its_interval_and_stops_cleanly() -> None:
    service, roles, _, _ = _service(pool=5)
    scheduler = ScanScheduler(service, 15)
    run = await scheduler.tick()
    assert run is not None
    assert run.trigger == "schedule"
    assert len(await _external_ids(roles)) == 5
    scheduler.start()
    await asyncio.sleep(0)
    await scheduler.stop()
