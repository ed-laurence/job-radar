"""Postgres adapter for roles, their events, kits and rejections. Every call runs in
`user_transaction`, so row-level security scopes it to the caller (ARCH-05, SEC-03); the
`user_id` filters only help the planner use the per-user indexes."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.engine import user_transaction
from app.adapters.db.jobs_mapping import (
    Sessions,
    diagnose_missing,
    kit_record,
    role_record,
    to_record,
)
from app.adapters.db.tables_jobs import (
    Rejection,
    Role,
    RoleEvent,
    RoleKit,
)
from app.domain.errors import NotFound
from app.domain.jobs.ports import (
    NewRoleEvent,
    RoleQuery,
    RoleWrite,
    Values,
)
from app.domain.jobs.records import (
    KitRecord,
    RejectionRecord,
    RoleEventRecord,
    RoleRecord,
)

_SORTS: dict[str, tuple[Any, ...]] = {
    "rank": (Role.rank, Role.id),
    "-found_at": (Role.found_at.desc(), Role.id),
    "-score": (Role.score.desc().nulls_last(), Role.id),
    "-posted_at": (Role.posted_at.desc().nulls_last(), Role.id),
}


def _role_query(user_id: uuid.UUID, query: RoleQuery) -> Select[Role]:
    stmt = select(Role).where(Role.user_id == user_id, Role.deleted_at.is_(None))
    if query.statuses:
        stmt = stmt.where(Role.status.in_(query.statuses))
    if query.stages:
        stmt = stmt.where(Role.stage.in_(query.stages))
    if query.track is not None:
        stmt = stmt.where(Role.track == query.track)
    if query.min_score is not None:
        stmt = stmt.where(Role.score >= query.min_score)
    seen = func.coalesce(Role.posted_at, Role.found_at)
    if query.seen_from is not None:
        stmt = stmt.where(seen >= query.seen_from)
    if query.seen_before is not None:
        stmt = stmt.where(seen < query.seen_before)
    if query.text:
        needle = query.text.lower()
        stmt = stmt.where(
            or_(
                func.lower(Role.title).contains(needle, autoescape=True),
                func.lower(Role.company).contains(needle, autoescape=True),
            )
        )
    return stmt.order_by(*_SORTS.get(query.sort, _SORTS["rank"]))


class PgRoles:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def list_roles(
        self, user_id: uuid.UUID, query: RoleQuery, offset: int, limit: int
    ) -> list[RoleRecord]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.scalars(_role_query(user_id, query).offset(offset).limit(limit))
            return [role_record(row) for row in rows]

    async def get_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> RoleRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.get(Role, role_id)
            if row is None or row.deleted_at is not None:
                return None
            return role_record(row, await session.get(RoleKit, role_id))

    async def insert_role(
        self, user_id: uuid.UUID, values: Values, events: Sequence[NewRoleEvent]
    ) -> RoleRecord:
        async with user_transaction(self._sessions, user_id) as session:
            row = Role(user_id=user_id, **values)
            session.add(row)
            await session.flush()
            _add_events(session, user_id, row.id, events)
            await session.flush()
            await session.refresh(row)
            return role_record(row)

    async def write_role(self, user_id: uuid.UUID, change: RoleWrite) -> RoleRecord:
        async with user_transaction(self._sessions, user_id) as session:
            stmt = update(Role).where(Role.id == change.role_id, Role.deleted_at.is_(None))
            if change.version is not None:
                stmt = stmt.where(Role.version == change.version)
            values = dict(change.values)
            if values:
                values["version"] = Role.version + 1
            else:
                values["id"] = Role.id
            row = await session.scalar(stmt.values(**values).returning(Role))
            if row is None:
                await diagnose_missing(session, Role, change.role_id, "role")
            role = cast("Role", row)
            _add_events(session, user_id, role.id, change.events)
            if change.rejection is not None:
                await _upsert_rejection(session, user_id, role.id, change.rejection)
            await session.flush()
            return role_record(role, await session.get(RoleKit, role.id))

    async def column_ranks(self, user_id: uuid.UUID, status: str) -> list[tuple[uuid.UUID, str]]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.execute(
                select(Role.id, Role.rank)
                .where(Role.user_id == user_id, Role.status == status, Role.deleted_at.is_(None))
                .order_by(Role.rank, Role.id)
            )
            return [(row[0], row[1]) for row in rows]

    async def set_ranks(self, user_id: uuid.UUID, ranks: Mapping[uuid.UUID, str]) -> None:
        if not ranks:
            return
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(
                update(Role), [{"id": role_id, "rank": rank} for role_id, rank in ranks.items()]
            )

    async def list_events(
        self,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        kinds: Sequence[str],
        offset: int,
        limit: int,
    ) -> list[RoleEventRecord]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.scalars(
                select(RoleEvent)
                .where(RoleEvent.role_id == role_id, RoleEvent.kind.in_(kinds))
                .order_by(RoleEvent.at.desc(), RoleEvent.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return [to_record(RoleEventRecord, row) for row in rows]

    async def save_kit(self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values) -> KitRecord:
        async with user_transaction(self._sessions, user_id) as session:
            stmt = insert(RoleKit).values(role_id=role_id, user_id=user_id, **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[RoleKit.role_id],
                set_={**values, "version": RoleKit.version + 1, "updated_at": func.now()},
            )
            row = await session.scalar(stmt.returning(RoleKit))
            return cast("KitRecord", kit_record(row))

    async def get_rejection(self, user_id: uuid.UUID, role_id: uuid.UUID) -> RejectionRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(select(Rejection).where(Rejection.role_id == role_id))
            return to_record(RejectionRecord, row) if row is not None else None

    async def update_rejection(
        self, user_id: uuid.UUID, role_id: uuid.UUID, values: Values
    ) -> RejectionRecord:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(
                update(Rejection)
                .where(Rejection.role_id == role_id)
                .values(**values, version=Rejection.version + 1)
                .returning(Rejection)
            )
            if row is None:
                raise NotFound("That role has no rejection recorded.")
            return to_record(RejectionRecord, row)

    async def list_rejections(self, user_id: uuid.UUID, limit: int) -> list[RejectionRecord]:
        stmt = (
            select(Rejection)
            .join(Role, Role.id == Rejection.role_id)
            .where(Rejection.user_id == user_id, Role.deleted_at.is_(None))
            .order_by(Rejection.rejected_at.desc().nulls_last(), Rejection.id)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(RejectionRecord, row) for row in await session.scalars(stmt)]


def _add_events(
    session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, events: Sequence[NewRoleEvent]
) -> None:
    session.add_all(
        RoleEvent(user_id=user_id, role_id=role_id, **dataclasses.asdict(event)) for event in events
    )


async def _upsert_rejection(
    session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, values: Values
) -> None:
    stmt = insert(Rejection).values(user_id=user_id, role_id=role_id, **values)
    keep = {"rejected_at", "updated_at"} | {k for k in ("channel",) if values.get(k)}
    if values.get("reason_categories"):
        keep.add("reason_categories")
    excluded = {name: getattr(stmt.excluded, name) for name in keep}
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Rejection.role_id],
            set_={**excluded, "version": Rejection.version + 1},
        )
    )
