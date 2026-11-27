"""Lease watchdog: reclaim expired path leases, and check that Identity Platform holds exactly
the provisioned accounts (sign-up is off, so a stray account is an alert).
`python -m app.jobs.watchdog`."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from typing import Protocol

from app.compose.devboard import board_stores, job_actor, job_run_log
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.schedule import JobName, JobReport, run_job
from app.main import Container, build_container
from app.settings import Settings


class IdentityDirectory(Protocol):
    async def count_users(self) -> int: ...


class StaticIdentityDirectory:
    """Fake directory: reports a fixed account count (offline dev and tests)."""

    def __init__(self, count: int) -> None:
        self._count = count

    async def count_users(self) -> int:
        return self._count


def directory_for(container: Container) -> IdentityDirectory | None:
    if container.settings.auth == "fake":
        return StaticIdentityDirectory(len(container.settings.allowlist().entries))
    # The Identity Platform admin adapter isn't built yet (an open question in the handoff).
    return None


async def run(
    container: Container,
    *,
    directory: IdentityDirectory | None = None,
    leases: LeaseService | None = None,
) -> JobReport:
    leases = leases or LeaseService(board_stores(container).leases, container.clock)
    users = directory or directory_for(container)
    expected = len(container.settings.allowlist().entries)

    async def body(report: JobReport) -> None:
        reclaimed = await leases.reclaim_expired(job_actor(container))
        report.items_seen = report.items_new = reclaimed
        report.note(f"Reclaimed {reclaimed} expired leases.")
        if users is None:
            report.note("Identity check skipped: no directory adapter for this auth mode.")
            return
        found = await users.count_users()
        if found != expected:
            report.fail(
                "unexpected_user_count",
                f"Identity Platform has {found} accounts; {expected} are provisioned.",
            )
            return
        report.note(f"Identity Platform has the {expected} provisioned accounts.")

    return await run_job(JobName.WATCHDOG, job_run_log(container), container.clock, body)


def main() -> int:
    async def go() -> JobReport:
        container = build_container(Settings())
        try:
            return await run(container)
        finally:
            await container.aclose()

    report = asyncio.run(go())
    sys.stdout.write(json.dumps(asdict(report)) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
