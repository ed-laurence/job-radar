"""Scan area (spec tag Scan): the job source, the scan service, its routes and (local only) the
in-process schedule. Installed after `jobs`, whose role store and scorer it reuses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.db.engine import session_factory
from app.adapters.db.scan import (
    PgKnownRoles,
    PgScanAccounts,
    PgScanLock,
    PgScanRuns,
    PgSeedFeed,
)
from app.adapters.fakes.jobs import MemoryRoles
from app.adapters.fakes.scan import (
    MemoryKnownRoles,
    MemoryScanAccounts,
    MemoryScanLock,
    MemoryScanRuns,
)
from app.adapters.scrapers.seed_feed import MemorySeedFeed
from app.api import scan as scan_routes
from app.compose.devboard import job_run_log
from app.compose.jobs import JobsRuntime, ScoringRoleScorer, build_services, job_stores
from app.domain.devboard.schedule import JOB_CAPS, Budget, JobName
from app.domain.jobs.scan import ScanScheduler, ScanService

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.main import Container


def scan_service(container: Container, runtime: JobsRuntime | None = None) -> ScanService:
    settings = container.settings
    stores = runtime.stores if runtime is not None else job_stores(container)
    scoring = (
        runtime.services if runtime is not None else build_services(container, stores)
    ).scoring
    scorer = ScoringRoleScorer(scoring, settings.allowlist())
    budget = Budget(JOB_CAPS[JobName.SCAN], container.usage, container.clock)
    service = ScanService(
        source=MemorySeedFeed(container.clock, batch=settings.scan_batch_size),
        roles=stores.roles,
        known=MemoryKnownRoles(_memory_roles(stores.roles)),
        accounts=MemoryScanAccounts([e.user_id for e in settings.allowlist().entries]),
        runs=MemoryScanRuns(),
        lock=MemoryScanLock(),
        clock=container.clock,
        ids=container.ids,
        scorer=scorer,
        budget=budget,
        interval_minutes=settings.scan_interval_minutes,
    )
    if container.engine is not None:
        sessions = session_factory(container.engine)
        service.source = PgSeedFeed(sessions, container.clock, settings.scan_batch_size)
        service.known = PgKnownRoles(sessions)
        service.accounts = PgScanAccounts(sessions)
        service.runs = PgScanRuns(sessions)
        service.lock = PgScanLock(container.engine)
        service.job_log = job_run_log(container)
    return service


def _memory_roles(roles: object) -> MemoryRoles:
    # Without Postgres the jobs area always uses `MemoryRoles`; with it the fake is unused.
    return roles if isinstance(roles, MemoryRoles) else MemoryRoles()


def install(app: FastAPI, container: Container) -> None:
    runtime = getattr(app.state, "jobs_runtime", None)
    service = scan_service(container, runtime if isinstance(runtime, JobsRuntime) else None)
    app.state.scan = service
    interval = container.settings.scan_interval_minutes
    app.state.scan_scheduler = ScanScheduler(service, interval) if interval else None
    app.include_router(scan_routes.router)
