"""`role_evidence`: the latest full evidence test per role, under row-level security."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction
from app.adapters.db.tables_jobs import RoleEvidence
from app.domain.jobs.evidence import Verdict
from app.domain.jobs.evidence_test import (
    EvidenceTestItem,
    EvidenceTestRecord,
    Match,
    Source,
    TeamCheck,
    TeamStatus,
    record_uses,
)

type Json = dict[str, object]


def to_json(record: EvidenceTestRecord) -> Json:
    return {
        "verdict": record.verdict,
        "verdict_text": record.verdict_text,
        "shown": record.shown,
        "total": record.total,
        "from_records": record.from_records,
        "team_check": {"status": record.team_check.status, "text": record.team_check.text},
        "items": [
            {
                "requirement": item.requirement,
                "match": item.match,
                "source": item.source,
                "why": item.why,
                "quote": item.quote,
                "record_id": str(item.record_id) if item.record_id else None,
                "record_title": item.record_title,
            }
            for item in record.items
        ],
    }


def _item(raw: Json) -> EvidenceTestItem:
    record_id = raw.get("record_id")
    return EvidenceTestItem(
        requirement=str(raw["requirement"]),
        match=cast("Match", raw["match"]),
        source=cast("Source", raw["source"]),
        why=str(raw.get("why") or ""),
        quote=cast("str | None", raw.get("quote")),
        record_id=uuid.UUID(str(record_id)) if record_id else None,
        record_title=cast("str | None", raw.get("record_title")),
    )


def from_row(row: RoleEvidence) -> EvidenceTestRecord:
    result = row.result
    team = cast("Json", result["team_check"])
    return EvidenceTestRecord(
        role_id=row.role_id,
        verdict=cast("Verdict", result["verdict"]),
        verdict_text=str(result["verdict_text"]),
        shown=int(cast("int", result["shown"])),
        total=int(cast("int", result["total"])),
        from_records=int(cast("int", result["from_records"])),
        items=tuple(_item(cast("Json", item)) for item in cast("list[object]", result["items"])),
        team_check=TeamCheck(status=cast("TeamStatus", team["status"]), text=str(team["text"])),
        checked_at=row.checked_at,
        model=row.model,
    )


class PgEvidence:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def latest(self, user_id: uuid.UUID, role_id: uuid.UUID) -> EvidenceTestRecord | None:
        async with user_transaction(self._sessions, user_id) as session:
            row = await session.scalar(
                select(RoleEvidence).where(
                    RoleEvidence.user_id == user_id, RoleEvidence.role_id == role_id
                )
            )
            return from_row(row) if row is not None else None

    async def save(self, user_id: uuid.UUID, record: EvidenceTestRecord) -> None:
        values = {
            "user_id": user_id,
            "role_id": record.role_id,
            "result": to_json(record),
            "model": record.model,
            "checked_at": record.checked_at,
        }
        stmt = insert(RoleEvidence).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[RoleEvidence.role_id],
            set_={
                "result": stmt.excluded.result,
                "model": stmt.excluded.model,
                "checked_at": stmt.excluded.checked_at,
                "updated_at": func.now(),
            },
        )
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(stmt)

    async def record_uses(self, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.scalars(
                select(RoleEvidence.result).where(RoleEvidence.user_id == user_id)
            )
            tests = [_shown_records(result) for result in rows]
        return record_uses(tests)


def _shown_records(result: Json) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    for raw in cast("list[object]", result.get("items") or []):
        item = cast("Json", raw) if isinstance(raw, dict) else {}
        record_id = item.get("record_id")
        if item.get("match") == "shown" and record_id:
            try:
                found.append(uuid.UUID(str(record_id)))
            except ValueError:
                continue
    return found
