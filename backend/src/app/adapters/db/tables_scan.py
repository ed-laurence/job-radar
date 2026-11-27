"""Tables added in migration 0004: the held-back seed pool, scan runs, profile documents."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.base import (
    JSONB_COL,
    Base,
    created,
    one_of,
    owner,
    pk,
    required_stamp,
    stamp,
    updated,
)

type Json = dict[str, object]


class RolePool(Base):
    """Seed roles held back until a scan releases them. Never listed or counted."""

    __tablename__ = "role_pool"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id"),
        Index("ix_role_pool_user_waiting", "user_id", "released_at", "posted_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    source: Mapped[str]
    external_id: Mapped[str]
    posted_at: Mapped[datetime | None] = stamp()
    payload: Mapped[Json] = mapped_column(JSONB_COL, nullable=False)
    released_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()


class ProfileDocuments(Base):
    """The per-user profile document manifest (the files stay in object storage)."""

    __tablename__ = "profile_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    manifest: Mapped[Json] = mapped_column(JSONB_COL, nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class ScanRun(Base):
    __tablename__ = "scan_runs"
    __table_args__ = (
        one_of("trigger", ("manual", "schedule")),
        one_of("status", ("running", "ok", "no_new_roles", "failed")),
        Index("ix_scan_runs_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    started_at: Mapped[datetime] = required_stamp()
    finished_at: Mapped[datetime | None] = stamp()
    trigger: Mapped[str]
    source: Mapped[str]
    status: Mapped[str]
    fetched: Mapped[int] = mapped_column(server_default=text("0"))
    new: Mapped[int] = mapped_column(server_default=text("0"))
    duplicates: Mapped[int] = mapped_column(server_default=text("0"))
    scored: Mapped[int] = mapped_column(server_default=text("0"))
    failed: Mapped[int] = mapped_column(server_default=text("0"))
    pool_remaining: Mapped[int] = mapped_column(server_default=text("0"))
    note: Mapped[str | None]
    created_at: Mapped[datetime] = created()
