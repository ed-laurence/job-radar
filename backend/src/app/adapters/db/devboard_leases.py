"""Postgres adapter for path leases, under `user_transaction` (SEC-03).

Lease grants serialise on a transaction-scoped advisory lock, so the overlap check and the
insert are atomic across processes without serialization-failure retries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.devboard_mappers import (
    lease_record,
)
from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_board import (
    Lease,
    LeaseWait,
)
from app.domain.devboard.model import (
    LeaseKind,
    LeaseRecord,
)
from app.domain.devboard.paths import overlaps
from app.domain.ports.devboard import LeaseClaim, LeaseOutcome

type Sessions = async_sessionmaker[AsyncSession]

_LEASE_LOCK = text("SELECT pg_advisory_xact_lock(7022891373)")


def _live(at: datetime) -> tuple[Any, ...]:
    return (Lease.released_at.is_(None), Lease.expires_at > at)


class PgLeaseStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def claim(self, user_id: uuid.UUID, claim: LeaseClaim) -> LeaseOutcome:
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(_LEASE_LOCK)
            live = [
                lease_record(row)
                for row in (await session.scalars(select(Lease).where(*_live(claim.at)))).all()
            ]
            conflicts = tuple(
                lease
                for lease in live
                if lease.run_id != claim.run_id
                and any(overlaps(lease.path, path) for path, _ in claim.paths)
            )
            if conflicts:
                await session.execute(
                    insert(LeaseWait),
                    [
                        {"run_id": claim.run_id, "path": path, "queued_at": claim.at}
                        for path, _ in claim.paths
                    ],
                )
                return LeaseOutcome(granted=(), conflicts=conflicts)
            held = {lease.path: lease for lease in live if lease.run_id == claim.run_id}
            granted = [
                await self._grant(session, claim, path, kind, held.get(path))
                for path, kind in claim.paths
            ]
            return LeaseOutcome(granted=tuple(granted), conflicts=())

    async def _grant(
        self,
        session: AsyncSession,
        claim: LeaseClaim,
        path: str,
        kind: LeaseKind,
        held: LeaseRecord | None,
    ) -> LeaseRecord:
        if held is not None:
            statement = (
                update(Lease)
                .where(Lease.id == held.id)
                .values(expires_at=func.greatest(Lease.expires_at, claim.expires_at))
                .returning(Lease)
            )
        else:
            values = {
                "run_id": claim.run_id,
                "task_id": claim.task_id,
                "path": path,
                "kind": kind.value,
                "granted_at": claim.at,
                "expires_at": claim.expires_at,
            }
            statement = insert(Lease).values(values).returning(Lease)
        row = await session.scalar(statement)
        assert row is not None
        return lease_record(row)

    async def renew(
        self, user_id: uuid.UUID, run_id: uuid.UUID, at: datetime, expires_at: datetime
    ) -> int:
        statement = (
            update(Lease)
            .where(Lease.run_id == run_id, *_live(at))
            .values(heartbeat_at=at, expires_at=expires_at)
            .returning(Lease.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return len((await session.scalars(statement)).all())

    async def release(
        self,
        user_id: uuid.UUID,
        at: datetime,
        *,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> int:
        if run_id is None and task_id is None:
            return 0
        owner = Lease.run_id == run_id if run_id is not None else Lease.task_id == task_id
        statement = (
            update(Lease)
            .where(owner, Lease.released_at.is_(None))
            .values(released_at=at)
            .returning(Lease.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return len((await session.scalars(statement)).all())

    async def reclaim_expired(self, user_id: uuid.UUID, at: datetime) -> int:
        statement = (
            update(Lease)
            .where(Lease.released_at.is_(None), Lease.expires_at <= at)
            .values(released_at=at)
            .returning(Lease.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return len((await session.scalars(statement)).all())

    async def active(self, user_id: uuid.UUID, at: datetime) -> list[LeaseRecord]:
        query = select(Lease).where(*_live(at)).order_by(Lease.path)
        async with user_transaction(self._sessions, user_id) as session:
            return [lease_record(row) for row in (await session.scalars(query)).all()]
