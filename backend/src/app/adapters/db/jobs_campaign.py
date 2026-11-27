"""Postgres adapter for the campaign plan's per-user state and the main page's extra counts,
all under `user_transaction` (row-level security)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.adapters.db.engine import user_transaction
from app.adapters.db.jobs_mapping import Sessions, to_record
from app.adapters.db.tables_jobs import (
    CampaignActivity,
    CampaignNote,
    CampaignTaskDone,
    Contact,
    Rejection,
    Role,
    RoleEvent,
)
from app.domain.jobs.ports import CampaignFacts, MainPageFacts, Values
from app.domain.jobs.records import ActivityRecord, RejectionSummaryRecord

OUTREACH_STATUSES = ("new", "drafted")


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


class PgCampaign:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def list_activity(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> list[ActivityRecord]:
        stmt = (
            select(CampaignActivity)
            .where(CampaignActivity.on_date >= start, CampaignActivity.on_date < end)
            .order_by(
                CampaignActivity.on_date.desc(),
                CampaignActivity.created_at.desc(),
                CampaignActivity.id.desc(),
            )
        )
        async with user_transaction(self._sessions, user_id) as session:
            return [to_record(ActivityRecord, row) for row in await session.scalars(stmt)]

    async def add_activity(self, user_id: uuid.UUID, values: Values) -> ActivityRecord:
        row = CampaignActivity(**{**values, "user_id": user_id})
        async with user_transaction(self._sessions, user_id) as session:
            session.add(row)
            await session.flush()
            return to_record(ActivityRecord, row)

    async def delete_activity(self, user_id: uuid.UUID, activity_id: uuid.UUID) -> bool:
        stmt = (
            delete(CampaignActivity)
            .where(CampaignActivity.id == activity_id)
            .returning(CampaignActivity.id)
        )
        async with user_transaction(self._sessions, user_id) as session:
            return (await session.scalar(stmt)) is not None

    async def done_tasks(self, user_id: uuid.UUID, week: int) -> set[str]:
        stmt = select(CampaignTaskDone.task_id).where(CampaignTaskDone.week == week)
        async with user_transaction(self._sessions, user_id) as session:
            return set(await session.scalars(stmt))

    async def set_task_done(
        self, user_id: uuid.UUID, task_id: str, week: int, at: datetime
    ) -> None:
        stmt = (
            insert(CampaignTaskDone)
            .values(user_id=user_id, task_id=task_id, week=week, done_at=at)
            .on_conflict_do_nothing()
        )
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(stmt)

    async def clear_task_done(self, user_id: uuid.UUID, task_id: str, week: int) -> None:
        stmt = delete(CampaignTaskDone).where(
            CampaignTaskDone.task_id == task_id, CampaignTaskDone.week == week
        )
        async with user_transaction(self._sessions, user_id) as session:
            await session.execute(stmt)

    async def note(self, user_id: uuid.UUID, key: str) -> str | None:
        stmt = select(CampaignNote.notes).where(CampaignNote.key == key)
        async with user_transaction(self._sessions, user_id) as session:
            return await session.scalar(stmt)

    async def facts(self, user_id: uuid.UUID, start: date, end: date) -> CampaignFacts:
        low, high = _midnight(start), _midnight(end)
        activity = select(CampaignActivity.on_date, CampaignActivity.kind).where(
            CampaignActivity.on_date >= start, CampaignActivity.on_date < end
        )
        applied = select(Role.applied_at).where(
            Role.deleted_at.is_(None), Role.applied_at >= low, Role.applied_at < high
        )
        followups = (
            select(RoleEvent.at)
            .join(Role, Role.id == RoleEvent.role_id)
            .where(
                Role.deleted_at.is_(None),
                RoleEvent.kind == "followed_up",
                RoleEvent.at >= low,
                RoleEvent.at < high,
            )
        )
        async with user_transaction(self._sessions, user_id) as session:
            acts = [(row[0], str(row[1])) for row in await session.execute(activity)]
            apps = [at for at in await session.scalars(applied) if at is not None]
            ups = list(await session.scalars(followups))
        return CampaignFacts(activity=tuple(acts), applied=tuple(apps), followups=tuple(ups))

    async def main_page(self, user_id: uuid.UUID, followup_before: datetime) -> MainPageFacts:
        outreach = (
            select(func.count())
            .select_from(Contact)
            .where(Contact.deleted_at.is_(None), Contact.status.in_(OUTREACH_STATUSES))
        )
        feedback = (
            select(func.count())
            .select_from(Rejection)
            .join(Role, Role.id == Rejection.role_id)
            .where(
                Role.deleted_at.is_(None),
                Rejection.summary.is_(None),
                func.cardinality(Rejection.reason_categories) == 0,
            )
        )
        due = (
            select(func.count())
            .select_from(Role)
            .where(
                Role.deleted_at.is_(None),
                Role.status == "applied",
                Role.followed_up_at.is_(None),
                Role.applied_at <= followup_before,
            )
        )
        # Deleted roles count too: the feed still found them.
        latest = select(func.max(Role.found_at)).where(Role.source != "manual")
        async with user_transaction(self._sessions, user_id) as session:
            return MainPageFacts(
                outreach_due=int(await session.scalar(outreach) or 0),
                feedback_due=int(await session.scalar(feedback) or 0),
                followups_due=int(await session.scalar(due) or 0),
                latest_feed_role_at=await session.scalar(latest),
            )

    async def rejection_summaries(
        self, user_id: uuid.UUID, offset: int, limit: int
    ) -> list[RejectionSummaryRecord]:
        stmt = (
            select(Rejection, Role.title, Role.company)
            .join(Role, Role.id == Rejection.role_id)
            .where(Role.deleted_at.is_(None))
            .order_by(Rejection.rejected_at.desc().nulls_last(), Rejection.id)
            .offset(offset)
            .limit(limit)
        )
        async with user_transaction(self._sessions, user_id) as session:
            rows = await session.execute(stmt)
            return [
                RejectionSummaryRecord(
                    role_id=rejection.role_id,
                    title=str(title),
                    company=company,
                    rejected_at=rejection.rejected_at,
                    has_text=bool(rejection.rejection_text or rejection.feedback_text),
                    summary=rejection.summary,
                    categories=tuple(rejection.reason_categories or ()),
                )
                for rejection, title, company in rows
            ]
