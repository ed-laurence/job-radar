"""Shared pieces of the job-search Postgres adapters: ORM row to domain record mapping and
the not-found-or-conflict diagnosis after a guarded update."""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.tables_jobs import (
    Contact,
    Role,
    RoleKit,
    Target,
)
from app.domain.errors import Conflict, NotFound
from app.domain.jobs.records import (
    KitAnswerRecord,
    KitRecord,
    RoleRecord,
)

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

type Sessions = async_sessionmaker[AsyncSession]
type Versioned = type[Role] | type[Contact] | type[Target]
# Contacts need a URL in the table (unique per user); one without gets a private placeholder.
URL_PLACEHOLDER = "urn:jobradar:contact:"
UNIQUE_VIOLATION = "23505"


def _plain(value: object) -> object:
    if isinstance(value, list):
        return tuple(cast("list[object]", value))
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value.startswith(URL_PLACEHOLDER):
        return None
    return value


def to_record[R: DataclassInstance](record_type: type[R], row: object, **extra: object) -> R:
    """Map an ORM row to a domain record by field name."""
    names = [item.name for item in dataclasses.fields(record_type)]
    values = {name: _plain(getattr(row, name)) for name in names if hasattr(row, name)}
    return record_type(**{**values, **extra})


def kit_record(row: RoleKit | None) -> KitRecord | None:
    if row is None or row.generated_at is None:
        return None
    answers = tuple(
        KitAnswerRecord(q=str(item.get("q", "")), a=str(item.get("a", "")))
        for item in cast("list[dict[str, object]]", row.answers or [])
    )
    return to_record(KitRecord, row, answers=answers)


def role_record(row: Role, kit: RoleKit | None = None) -> RoleRecord:
    return to_record(RoleRecord, row, stage=row.stage or "new", kit=kit_record(kit))


async def diagnose_missing(
    session: AsyncSession, orm: Versioned, row_id: uuid.UUID, noun: str
) -> None:
    current = await session.scalar(
        select(orm.version).where(orm.id == row_id, orm.deleted_at.is_(None))
    )
    if current is None:
        raise NotFound(f"That {noun} doesn't exist.")
    raise Conflict(f"The {noun} changed since you read it.", current_version=int(current))
