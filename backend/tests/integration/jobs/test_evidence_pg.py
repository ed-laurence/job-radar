"""`role_evidence` in Postgres: round trip, replace on re-run, and row-level security."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction
from app.adapters.db.evidence import PgEvidence
from app.domain.jobs.evidence_test import EvidenceTestItem, EvidenceTestRecord, TeamCheck

type Sessions = async_sessionmaker[AsyncSession]
AT = datetime(2026, 11, 26, 7, 2, tzinfo=UTC)


async def _role(sessions: Sessions, user_id: uuid.UUID) -> uuid.UUID:
    async with user_transaction(sessions, user_id) as session:
        found = await session.scalar(
            text(
                "INSERT INTO roles (user_id, source, title, found_at, rank)"
                " VALUES (:u, 'manual', 'Lead Data Engineer', now(), 'a') RETURNING id"
            ),
            {"u": user_id},
        )
    assert isinstance(found, uuid.UUID)
    return found


def _record(role_id: uuid.UUID, shown: int = 1) -> EvidenceTestRecord:
    return EvidenceTestRecord(
        role_id=role_id,
        verdict="consider",
        verdict_text=f"Consider · you show {shown} of 2 must-haves",
        shown=shown,
        total=2,
        from_records=1,
        items=(
            EvidenceTestItem(
                requirement="Observability",
                match="shown",
                source="records",
                why="Rolled it out.",
                quote="Rolled out tracing across 40 services.",
                record_id=uuid.UUID(int=9),
                record_title="Platform lead at Tailspin Toys",
            ),
            EvidenceTestItem(requirement="dbt", match="missing", source="none", why="None."),
        ),
        team_check=TeamCheck(status="not_stated", text="Team: not stated."),
        checked_at=AT,
        model="claude-sonnet-5-5",
    )


async def test_round_trip_replace_and_isolation(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    role_id = await _role(sessions, first)
    store = PgEvidence(sessions)
    assert await store.latest(first, role_id) is None
    await store.save(first, _record(role_id))
    assert await store.latest(first, role_id) == _record(role_id)
    await store.save(first, _record(role_id, shown=2))
    latest = await store.latest(first, role_id)
    assert latest is not None
    assert latest.shown == 2
    assert await store.latest(second, role_id) is None
    # Another user can't overwrite it: the upsert hits a row their policy hides.
    with pytest.raises(DBAPIError, match="row-level security"):
        await store.save(second, _record(role_id, shown=0))
    assert latest == await store.latest(first, role_id)


async def test_record_uses_count_each_role_once_and_only_shown_items(
    sessions: Sessions, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    first, second = two_users
    store = PgEvidence(sessions)
    one, two = await _role(sessions, first), await _role(sessions, first)
    await store.save(first, _record(one))
    await store.save(first, _record(two))
    assert (await store.record_uses(first)).get(uuid.UUID(int=9)) == 2
    assert await store.record_uses(second) == {}
