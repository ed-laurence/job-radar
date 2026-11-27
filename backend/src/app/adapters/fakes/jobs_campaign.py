"""In-memory `CampaignStore` (unit tests and offline dev). Reads roles, rejections and contacts
from the other in-memory stores so counts match the Postgres adapter."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from app.adapters.fakes.jobs import MemoryRoles, MemoryRows, build
from app.domain.jobs.ports import CampaignFacts, MainPageFacts, Values
from app.domain.jobs.records import ActivityRecord, ContactRecord, RejectionSummaryRecord

OUTREACH_STATUSES = frozenset({"new", "drafted"})


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


class MemoryCampaign:
    def __init__(self, roles: MemoryRoles, contacts: MemoryRows[ContactRecord]) -> None:
        self._roles = roles
        self._contacts = contacts
        self.activity: dict[tuple[uuid.UUID, uuid.UUID], ActivityRecord] = {}
        self.done: dict[tuple[uuid.UUID, str, int], datetime] = {}
        self.notes: dict[tuple[uuid.UUID, str], str] = {}

    async def list_activity(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> list[ActivityRecord]:
        rows = [
            row
            for (owner, _), row in self.activity.items()
            if owner == user_id and start <= row.on_date < end
        ]
        rows.sort(key=lambda r: (r.on_date, r.created_at, r.id), reverse=True)
        return rows

    async def add_activity(self, user_id: uuid.UUID, values: Values) -> ActivityRecord:
        record = build(ActivityRecord, values)
        self.activity[(user_id, record.id)] = record
        return record

    async def delete_activity(self, user_id: uuid.UUID, activity_id: uuid.UUID) -> bool:
        return self.activity.pop((user_id, activity_id), None) is not None

    async def done_tasks(self, user_id: uuid.UUID, week: int) -> set[str]:
        return {task for (owner, task, w) in self.done if owner == user_id and w == week}

    async def set_task_done(
        self, user_id: uuid.UUID, task_id: str, week: int, at: datetime
    ) -> None:
        self.done.setdefault((user_id, task_id, week), at)

    async def clear_task_done(self, user_id: uuid.UUID, task_id: str, week: int) -> None:
        self.done.pop((user_id, task_id, week), None)

    async def note(self, user_id: uuid.UUID, key: str) -> str | None:
        return self.notes.get((user_id, key))

    async def facts(self, user_id: uuid.UUID, start: date, end: date) -> CampaignFacts:
        low, high = _midnight(start), _midnight(end)
        roles = {
            (owner, role.id): role
            for (owner, _), role in self._roles.roles.items()
            if owner == user_id and role.deleted_at is None
        }
        activity = tuple(
            (row.on_date, row.kind)
            for (owner, _), row in self.activity.items()
            if owner == user_id and start <= row.on_date < end
        )
        applied = tuple(
            role.applied_at
            for role in roles.values()
            if role.applied_at is not None and low <= role.applied_at < high
        )
        followups = tuple(
            event.at
            for (owner, _), event in self._roles.events.items()
            if owner == user_id
            and event.kind == "followed_up"
            and (owner, event.role_id) in roles
            and low <= event.at < high
        )
        return CampaignFacts(activity=activity, applied=applied, followups=followups)

    async def main_page(self, user_id: uuid.UUID, followup_before: datetime) -> MainPageFacts:
        owned = [role for (owner, _), role in self._roles.roles.items() if owner == user_id]
        live = {role.id for role in owned if role.deleted_at is None}
        outreach = sum(
            1
            for (owner, _), contact in self._contacts.rows.items()
            if owner == user_id
            and contact.deleted_at is None
            and contact.status in OUTREACH_STATUSES
        )
        feedback = sum(
            1
            for (owner, role_id), rejection in self._roles.rejections.items()
            if owner == user_id
            and role_id in live
            and rejection.summary is None
            and not rejection.reason_categories
        )
        due = sum(
            1
            for role in owned
            if role.deleted_at is None
            and role.status == "applied"
            and role.followed_up_at is None
            and role.applied_at is not None
            and role.applied_at <= followup_before
        )
        found = [role.found_at for role in owned if role.source != "manual"]
        return MainPageFacts(
            outreach_due=outreach,
            feedback_due=feedback,
            followups_due=due,
            latest_feed_role_at=max(found) if found else None,
        )

    async def rejection_summaries(
        self, user_id: uuid.UUID, offset: int, limit: int
    ) -> list[RejectionSummaryRecord]:
        rows = await self._roles.list_rejections(user_id, offset + limit)
        out: list[RejectionSummaryRecord] = []
        for rejection in rows[offset:]:
            role = self._roles.roles[(user_id, rejection.role_id)]
            out.append(
                RejectionSummaryRecord(
                    role_id=rejection.role_id,
                    title=role.title,
                    company=role.company,
                    rejected_at=rejection.rejected_at,
                    has_text=bool(rejection.rejection_text or rejection.feedback_text),
                    summary=rejection.summary,
                    categories=rejection.reason_categories,
                )
            )
        return out
