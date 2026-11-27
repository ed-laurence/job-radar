"""Users, LLM spend and platform tables (data-model.md "Users and auth", "LLM and spend",
"Platform")."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    Double,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
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
    required_stamp,
    stamp,
    updated,
    version,
)


class User(Base):
    """Sys. Rows come from provisioning (and the seed job); there is no sign-up."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        UniqueConstraint("idp_uid"),
        CheckConstraint("email = lower(email)", name="email_lower"),
        one_of("role", vocab.APP_ROLE),
    )

    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str]
    role: Mapped[str]
    display_name: Mapped[str | None]
    idp_uid: Mapped[str | None]
    created_at: Mapped[datetime] = created()
    disabled_at: Mapped[datetime | None] = stamp()


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB_COL, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = version()
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        one_of("feature", vocab.LLM_FEATURE),
        one_of("provider", vocab.LLM_PROVIDER),
        one_of("guard_input", vocab.GUARD_RESULT),
        CheckConstraint(
            "guard_output IS NULL OR guard_output IN ('pass', 'block', 'redact')",
            name="guard_output",
        ),
        Index("ix_llm_calls_user_id_at", "user_id", "at"),
        Index("ix_llm_calls_feature_provider_at", "feature", "provider", "at"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = owner()
    at: Mapped[datetime] = required_stamp()
    feature: Mapped[str]
    provider: Mapped[str]
    model: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(server_default=text("0"))
    ok: Mapped[bool]
    error_code: Mapped[str | None]
    fallback_used: Mapped[bool] = mapped_column(server_default=text("false"))
    guard_input: Mapped[str] = mapped_column(server_default=text("'pass'"))
    guard_output: Mapped[str | None]
    role_id: Mapped[uuid.UUID | None] = opt_ref("roles.id")
    run_id: Mapped[uuid.UUID | None] = opt_ref("runs.id")
    research_run_id: Mapped[uuid.UUID | None] = opt_ref("research_runs.id")
    created_at: Mapped[datetime] = created()


class LlmBudgetUsage(Base):
    __tablename__ = "llm_budget_usage"
    __table_args__ = (PrimaryKeyConstraint("day", "feature"), one_of("feature", vocab.LLM_FEATURE))

    day: Mapped[date] = mapped_column(Date)
    feature: Mapped[str]
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (PrimaryKeyConstraint("key", "tier"), one_of("tier", vocab.RATE_TIER))

    key: Mapped[str]
    tier: Mapped[str]
    tokens: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    refilled_at: Mapped[datetime] = required_stamp()
    created_at: Mapped[datetime] = created()


class WebEvent(Base):
    """Sys. Partitioned by month on `day` (partitions are managed in SQL, see migration 0001)."""

    __tablename__ = "web_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", "day"),
        one_of("type", vocab.WEB_EVENT_TYPE),
        one_of("role_tag", vocab.APP_ROLE),
        CheckConstraint(
            "name IS NULL OR name IN ("
            + ", ".join(f"'{name}'" for name in vocab.WEB_EVENT_NAME)
            + ")",
            name="name",
        ),
        CheckConstraint(
            "metric IS NULL OR metric IN ("
            + ", ".join(f"'{metric}'" for metric in vocab.WEB_VITAL_METRIC)
            + ")",
            name="metric",
        ),
        CheckConstraint(
            "rating IS NULL OR rating IN ("
            + ", ".join(f"'{rating}'" for rating in vocab.WEB_VITAL_RATING)
            + ")",
            name="rating",
        ),
        Index("ix_web_events_day_type", "day", "type"),
        {"postgresql_partition_by": "RANGE (day)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(server_default=text("gen_random_uuid()"))
    day: Mapped[date] = mapped_column(Date)
    at: Mapped[datetime] = required_stamp()
    session_id: Mapped[str]
    role_tag: Mapped[str]
    type: Mapped[str]
    route: Mapped[str]
    name: Mapped[str | None]
    metric: Mapped[str | None]
    value: Mapped[float | None] = mapped_column(Double)
    rating: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        one_of("job", vocab.JOB_NAME),
        Index("ix_job_runs_job_started", "job", "started_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    job: Mapped[str]
    started_at: Mapped[datetime] = required_stamp()
    finished_at: Mapped[datetime | None] = stamp()
    ok: Mapped[bool | None]
    items_seen: Mapped[int] = mapped_column(server_default=text("0"))
    items_new: Mapped[int] = mapped_column(server_default=text("0"))
    error_code: Mapped[str | None]
    created_at: Mapped[datetime] = created()


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB_COL, nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()
