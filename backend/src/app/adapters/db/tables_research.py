"""Research-run table (user-owned: `user_id` + RLS). Its steps live in the shared `run_steps`
table (see `tables_board.RunStep`), so dev-board and research runs share one step stream."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db import vocab
from app.adapters.db.base import (
    TEXT_ARRAY,
    Base,
    created,
    one_of,
    owner,
    pk,
    ref,
    stamp,
    subset_of,
)


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        one_of("status", vocab.RUN_STATUS),
        subset_of("focus", vocab.RESEARCH_FOCUS),
        Index("ix_research_runs_user_target", "user_id", "target_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    target_id: Mapped[uuid.UUID] = ref("targets.id")
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    focus: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    summary: Mapped[str | None]
    sources: Mapped[list[str]] = mapped_column(TEXT_ARRAY, server_default=text("'{}'::text[]"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default=text("0"))
    started_at: Mapped[datetime | None] = stamp()
    finished_at: Mapped[datetime | None] = stamp()
    created_at: Mapped[datetime] = created()
