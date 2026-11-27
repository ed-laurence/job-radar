"""Job-search tables (data-model.md "Job search"). All user-owned: `user_id` + RLS."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db import vocab
from app.adapters.db.base import (
    JSONB_COL,
    TEXT_ARRAY,
    Base,
    created,
    one_of,
    opt_ref,
    owner,
    pk,
    rank,
    ref,
    required_stamp,
    stamp,
    subset_of,
    updated,
    version,
)

type Json = dict[str, object]
type JsonList = list[object]


def _nullable_one_of(column: str, values: tuple[str, ...]) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f'"{column}" IS NULL OR "{column}" IN ({quoted})', name=column)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id"),
        one_of("source", vocab.ROLE_SOURCE),
        one_of("status", vocab.ROLE_STATUS),
        _nullable_one_of("stage", vocab.ROLE_STAGE),
        _nullable_one_of("work_mode", vocab.WORK_MODE),
        _nullable_one_of("track", vocab.TRACK),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score"),
        Index("ix_roles_user_status_rank", "user_id", "status", "rank"),
        Index("ix_roles_user_found_at", "user_id", "found_at"),
        Index("ix_roles_user_stage", "user_id", "stage"),
        Index(
            "ix_roles_user_company_title",
            "user_id",
            func.lower(text("company")),
            func.lower(text("title")),
        ),
        # The age filter: `coalesce(posted_at, found_at)` (migration 0004).
        Index("ix_roles_user_seen", "user_id", func.coalesce(text("posted_at"), text("found_at"))),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    source: Mapped[str]
    external_id: Mapped[str | None]
    search_id: Mapped[str | None]
    url: Mapped[str | None]
    title: Mapped[str]
    company: Mapped[str | None]
    location: Mapped[str | None]
    work_mode: Mapped[str | None]
    pay: Mapped[str | None]
    posted_at: Mapped[datetime | None] = stamp()
    found_at: Mapped[datetime] = required_stamp()
    description: Mapped[str | None]
    stage: Mapped[str | None]
    filter_reason: Mapped[str | None]
    track: Mapped[str | None]
    mission: Mapped[str | None]
    score: Mapped[int | None]
    score_breakdown: Mapped[Json | None] = mapped_column(JSONB_COL)
    why: Mapped[str | None]
    risks: Mapped[list[str] | None] = mapped_column(TEXT_ARRAY)
    status: Mapped[str] = mapped_column(server_default=text("'new'"))
    rank: Mapped[str] = rank()
    applied_at: Mapped[datetime | None] = stamp()
    followed_up_at: Mapped[datetime | None] = stamp()
    notified_at: Mapped[datetime | None] = stamp()
    notes: Mapped[str | None]
    raw: Mapped[Json | None] = mapped_column(JSONB_COL)
    release_at: Mapped[datetime | None] = stamp()
    deleted_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class RoleKit(Base):
    __tablename__ = "role_kits"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = owner()
    cv: Mapped[str | None]
    cv_note: Mapped[str | None]
    cv_tweaks: Mapped[JsonList | None] = mapped_column(JSONB_COL)
    cover_note: Mapped[str | None]
    letter_to: Mapped[str | None]
    letter_re: Mapped[str | None]
    outreach: Mapped[str | None]
    contact: Mapped[str | None]
    answers: Mapped[JsonList | None] = mapped_column(JSONB_COL)
    model: Mapped[str | None]
    provider: Mapped[str | None]
    generated_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class RoleEvent(Base):
    __tablename__ = "role_events"
    __table_args__ = (
        one_of("kind", vocab.ROLE_EVENT_KIND),
        Index("ix_role_events_user_at", "user_id", "at"),
        Index("ix_role_events_role_at", "role_id", "at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    at: Mapped[datetime] = required_stamp()
    kind: Mapped[str]
    from_value: Mapped[str | None]
    to_value: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class RoleChat(Base):
    __tablename__ = "role_chats"
    __table_args__ = (
        one_of("author", vocab.CHAT_AUTHOR),
        Index("ix_role_chats_role_created", "role_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    author: Mapped[str]
    content: Mapped[str]
    created_at: Mapped[datetime] = created()


class RoleChatFile(Base):
    __tablename__ = "role_chat_files"
    __table_args__ = (one_of("kind", vocab.FILE_KIND),)

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    chat_id: Mapped[uuid.UUID] = ref("role_chats.id")
    name: Mapped[str]
    media_type: Mapped[str]
    kind: Mapped[str]
    object_key: Mapped[str]
    text: Mapped[str | None]
    size_bytes: Mapped[int]
    created_at: Mapped[datetime] = created()


class Rejection(Base):
    __tablename__ = "rejections"
    __table_args__ = (
        UniqueConstraint("role_id"),
        subset_of("reason_categories", vocab.REJECTION_CATEGORY),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    stage: Mapped[str | None]
    rejected_at: Mapped[datetime | None] = stamp()
    rejection_text: Mapped[str | None]
    from_name: Mapped[str | None]
    channel: Mapped[str | None]
    request_message: Mapped[str | None]
    request_problems: Mapped[JsonList | None] = mapped_column(JSONB_COL)
    request_status: Mapped[str | None]
    request_sent_at: Mapped[datetime | None] = stamp()
    feedback_text: Mapped[str | None]
    feedback_at: Mapped[datetime | None] = stamp()
    reason_categories: Mapped[list[str]] = mapped_column(
        TEXT_ARRAY, server_default=text("'{}'::text[]")
    )
    reasons: Mapped[JsonList | None] = mapped_column(JSONB_COL)
    summary: Mapped[str | None]
    lesson: Mapped[str | None]
    use_in_kits: Mapped[bool] = mapped_column(server_default=text("false"))
    analysed_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Followup(Base):
    __tablename__ = "followups"
    __table_args__ = (Index("ix_followups_user_role_priority", "user_id", "role_id", "priority"),)

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    priority: Mapped[int] = mapped_column(server_default=text("0"))
    target_name: Mapped[str | None]
    target_role: Mapped[str | None]
    source: Mapped[str | None]
    contact_id: Mapped[uuid.UUID | None] = opt_ref("contacts.id")
    channel: Mapped[str | None]
    email: Mapped[str | None]
    url: Mapped[str | None]
    why: Mapped[str | None]
    message: Mapped[str | None]
    problems: Mapped[JsonList | None] = mapped_column(JSONB_COL)
    status: Mapped[str] = mapped_column(server_default=text("'suggested'"))
    sent_at: Mapped[datetime | None] = stamp()
    notes: Mapped[str | None]
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class RoleRecruiter(Base):
    __tablename__ = "role_recruiters"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    name: Mapped[str]
    agency: Mapped[str | None]
    url: Mapped[str | None]
    email: Mapped[str | None]
    contact_id: Mapped[uuid.UUID | None] = opt_ref("contacts.id")
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("user_id", "url"),
        one_of("status", vocab.CONTACT_STATUS),
        _nullable_one_of("closeness", vocab.CLOSENESS),
        Index("ix_contacts_user_company_norm", "user_id", "company_norm"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    url: Mapped[str]
    first_name: Mapped[str | None]
    last_name: Mapped[str | None]
    email: Mapped[str | None]
    company: Mapped[str | None]
    company_norm: Mapped[str | None]
    position: Mapped[str | None]
    connected_on: Mapped[date | None] = mapped_column(Date)
    au_company: Mapped[str | None]
    outreach_score: Mapped[int | None]
    outreach_reason: Mapped[str | None]
    analysed_at: Mapped[datetime | None] = stamp()
    closeness: Mapped[str | None]
    message: Mapped[str | None]
    message_at: Mapped[datetime | None] = stamp()
    status: Mapped[str] = mapped_column(server_default=text("'new'"))
    sent_at: Mapped[datetime | None] = stamp()
    notes: Mapped[str | None]
    deleted_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Target(Base):
    __tablename__ = "targets"
    __table_args__ = (
        UniqueConstraint("user_id", "name_key"),
        one_of("lane", vocab.TARGET_LANE),
        one_of("status", vocab.TARGET_STATUS),
        one_of("origin", vocab.TARGET_ORIGIN),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    name: Mapped[str]
    name_key: Mapped[str]
    lane: Mapped[str]
    why: Mapped[str | None]
    approach: Mapped[str | None]
    caveat: Mapped[str | None]
    sources: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    status: Mapped[str] = mapped_column(server_default=text("'suggested'"))
    origin: Mapped[str]
    notes: Mapped[str | None]
    message: Mapped[str | None]
    message_to: Mapped[str | None]
    message_check: Mapped[str | None]
    message_url: Mapped[str | None]
    approached_at: Mapped[datetime | None] = stamp()
    deleted_at: Mapped[datetime | None] = stamp()
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class CampaignActivity(Base):
    __tablename__ = "campaign_activity"
    __table_args__ = (Index("ix_campaign_activity_user_date", "user_id", "on_date"),)

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    on_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str]
    who: Mapped[str | None]
    company: Mapped[str | None]
    role_id: Mapped[uuid.UUID | None] = opt_ref("roles.id")
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class CampaignTaskDone(Base):
    __tablename__ = "campaign_task_done"
    __table_args__ = (PrimaryKeyConstraint("user_id", "task_id", "week"),)

    user_id: Mapped[uuid.UUID] = owner()
    task_id: Mapped[str]
    week: Mapped[int]
    done_at: Mapped[datetime] = required_stamp()
    created_at: Mapped[datetime] = created()


class CampaignNote(Base):
    __tablename__ = "campaign_notes"
    __table_args__ = (PrimaryKeyConstraint("user_id", "key"),)

    user_id: Mapped[uuid.UUID] = owner()
    key: Mapped[str]
    notes: Mapped[str]
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class Experience(Base):
    __tablename__ = "experience"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    requirement: Mapped[str]
    role_id: Mapped[uuid.UUID | None] = opt_ref("roles.id")
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class ExperienceEntry(Base):
    __tablename__ = "experience_entries"

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    experience_id: Mapped[uuid.UUID] = ref("experience.id")
    context: Mapped[str | None]
    detail: Mapped[str]
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class RoleEvidence(Base):
    """The latest full evidence test per role (migration 0005); the result is JSON."""

    __tablename__ = "role_evidence"
    __table_args__ = (UniqueConstraint("role_id"),)

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    role_id: Mapped[uuid.UUID] = ref("roles.id")
    result: Mapped[Json] = mapped_column(JSONB_COL, nullable=False)
    model: Mapped[str]
    checked_at: Mapped[datetime] = required_stamp()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()
