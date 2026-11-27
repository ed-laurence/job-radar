"""Legacy feed replay, superseded by `app.jobs.scan` (`main` here now runs
the scan). Release up to 20 held-back seed roles (`roles.release_at <= now`) as new
postings, then score them through the jobs area's `RoleScorer` (from `app.compose.jobs`), within
the job's daily spend cap. `python -m app.jobs.feed_replay`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import session_factory, system_transaction, user_transaction
from app.compose.devboard import job_run_log
from app.compose.jobs import role_scorer
from app.domain.devboard.schedule import JOB_CAPS, Budget, JobName, JobReport, run_job
from app.domain.errors import DomainError
from app.domain.jobs.ports import RoleScorer
from app.jobs import scan
from app.main import Container

RELEASE_LIMIT = 20

_USERS = text("SELECT id FROM users WHERE disabled_at IS NULL ORDER BY created_at, id")
_RELEASE = text(
    "UPDATE roles SET release_at = NULL, found_at = :now, status = 'new', updated_at = :now,"
    " version = version + 1 WHERE id IN (SELECT id FROM roles WHERE user_id = :user_id"
    " AND release_at IS NOT NULL AND release_at <= :now AND deleted_at IS NULL"
    " ORDER BY release_at, id LIMIT :limit FOR UPDATE SKIP LOCKED) RETURNING id"
)


@dataclass(frozen=True)
class ReleasedRole:
    user_id: uuid.UUID
    role_id: uuid.UUID


class HeldBackRoles(Protocol):
    async def release(self, now: datetime, limit: int) -> list[ReleasedRole]: ...


class PgHeldBackRoles:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def release(self, now: datetime, limit: int) -> list[ReleasedRole]:
        async with system_transaction(self._sessions) as session:
            users = [row[0] for row in await session.execute(_USERS)]
        released: list[ReleasedRole] = []
        for user_id in users:
            room = limit - len(released)
            if room <= 0:
                break
            # roles are user-owned: each user's rows are released under that user's RLS scope.
            async with user_transaction(self._sessions, user_id) as session:
                params = {"now": now, "user_id": user_id, "limit": room}
                rows = await session.execute(_RELEASE, params)
                released.extend(ReleasedRole(user_id, row[0]) for row in rows)
        return released


@dataclass
class InMemoryHeldBackRoles:
    held: list[tuple[ReleasedRole, datetime]] = field(
        default_factory=list[tuple[ReleasedRole, datetime]]
    )

    async def release(self, now: datetime, limit: int) -> list[ReleasedRole]:
        due = sorted((item for item in self.held if item[1] <= now), key=lambda item: item[1])
        chosen = due[:limit]
        self.held = [item for item in self.held if item not in chosen]
        return [role for role, _ in chosen]


def held_back_roles(container: Container) -> HeldBackRoles:
    if container.engine is None:
        return InMemoryHeldBackRoles()
    return PgHeldBackRoles(session_factory(container.engine))


async def run_scored(container: Container) -> JobReport:
    """The scheduled run: release held-back roles and score them with the jobs area's scorer."""
    return await run(container, scorer=role_scorer(container))


async def run(
    container: Container,
    *,
    roles: HeldBackRoles | None = None,
    scorer: RoleScorer | None = None,
) -> JobReport:
    """Without a `scorer` the roles are released unscored."""
    source = roles or held_back_roles(container)
    scoring = scorer
    budget = Budget(JOB_CAPS[JobName.FEED_REPLAY], container.usage, container.clock)

    async def body(report: JobReport) -> None:
        released = await source.release(container.clock.now(), RELEASE_LIMIT)
        report.items_seen = report.items_new = len(released)
        report.note(f"Released {len(released)} held-back roles.")
        if scoring is None:
            report.note("No scoring service in this container: released without scores.")
            return
        scored = 0
        for role in released:
            if not await budget.has_room():
                report.note("Daily spend cap reached: the rest stay unscored.")
                break
            try:
                await scoring.score_role(role.user_id, role.role_id)
            except DomainError as error:
                report.note(f"Scoring stopped: {error.code}.")
                break
            scored += 1
        report.note(f"Scored {scored} roles.")

    return await run_job(JobName.FEED_REPLAY, job_run_log(container), container.clock, body)


def main() -> int:
    """The old job name now runs the scan (`app.jobs.scan`)."""
    return scan.main()


if __name__ == "__main__":
    raise SystemExit(main())
