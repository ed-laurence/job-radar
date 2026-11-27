"""Task chat tables (user-owned, RLS): each account's own conversation about a shared card, and
the edits Claude proposed in it. A proposal never changes the card until it is accepted."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db import vocab
from app.adapters.db.base import (
    JSONB_COL,
    Base,
    created,
    one_of,
    opt_ref,
    owner,
    pk,
    ref,
    stamp,
)


class TaskChatProposal(Base):
    __tablename__ = "task_chat_proposals"
    __table_args__ = (
        one_of("status", vocab.TASK_CHAT_PROPOSAL_STATUS),
        Index("ix_task_chat_proposals_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    status: Mapped[str]
    reason: Mapped[str]
    changes: Mapped[dict[str, object]] = mapped_column(JSONB_COL, nullable=False)
    base_version: Mapped[int]
    decided_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()


class TaskChatMessage(Base):
    __tablename__ = "task_chat_messages"
    __table_args__ = (
        one_of("author", vocab.CHAT_AUTHOR),
        Index("ix_task_chat_messages_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    task_id: Mapped[uuid.UUID] = ref("dev_tasks.id")
    author: Mapped[str]
    content: Mapped[str]
    proposal_id: Mapped[uuid.UUID | None] = opt_ref("task_chat_proposals.id")
    created_at: Mapped[datetime] = created()


__all__ = ["TaskChatMessage", "TaskChatProposal"]
