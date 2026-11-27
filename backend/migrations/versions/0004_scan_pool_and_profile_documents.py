"""Scan runs, the held-back seed pool, profile documents in the database, the roles age index.

Revision ID: 0004
Revises: 0002
Create Date: 2026-11-27

- `role_pool` (user-owned, RLS): seed roles held back from the visible list until a scan
  releases them. Nothing else reads it, so the pool never shows in a list or a count.
- `scan_runs` (system): one row per scan with its counts.
- `profile_documents` (user-owned, RLS): the per-user document manifest, which used to be a
  `manifest.json` object in storage. The files themselves stay in storage.
- `ix_roles_user_seen`: the roles age filter runs on `coalesce(posted_at, found_at)`.
- `job_runs.job` accepts `scan` (the feed replay job's new name).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "jobradar_app"
_CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_OWNED = ("role_pool", "profile_documents")
_JOBS_OLD = "('feed-replay', 'digest', 'rollup', 'watchdog', 'evals', 'seed', 'tester-reset')"
_JOBS_NEW = (
    "('scan', 'feed-replay', 'digest', 'rollup', 'watchdog', 'evals', 'seed', 'tester-reset')"
)
_STAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "role_pool",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("posted_at", _STAMP, nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("released_at", _STAMP, nullable=True),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_role_pool_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_pool")),
        sa.UniqueConstraint(
            "user_id", "source", "external_id", name=op.f("uq_role_pool_user_id_source_external_id")
        ),
    )
    op.create_index(
        "ix_role_pool_user_waiting", "role_pool", ["user_id", "released_at", "posted_at"]
    )
    op.create_table(
        "profile_documents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_profile_documents_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_profile_documents")),
    )
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("started_at", _STAMP, nullable=False),
        sa.Column("finished_at", _STAMP, nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("scored", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pool_remaining", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "\"trigger\" IN ('manual', 'schedule')", name=op.f("ck_scan_runs_trigger")
        ),
        sa.CheckConstraint(
            "\"status\" IN ('running', 'ok', 'no_new_roles', 'failed')",
            name=op.f("ck_scan_runs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_runs")),
    )
    op.create_index("ix_scan_runs_started", "scan_runs", ["started_at"])
    op.create_index(
        "ix_roles_user_seen",
        "roles",
        ["user_id", sa.literal_column("COALESCE(posted_at, found_at)")],
        unique=False,
    )
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT ck_job_runs_job")
    op.execute(f'ALTER TABLE job_runs ADD CONSTRAINT ck_job_runs_job CHECK ("job" IN {_JOBS_NEW})')
    for table in _OWNED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY owner ON {table} FOR ALL "
            f"USING (user_id = {_CURRENT_USER}) WITH CHECK (user_id = {_CURRENT_USER})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON scan_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DELETE FROM job_runs WHERE job = 'scan'")
    op.execute("ALTER TABLE job_runs DROP CONSTRAINT ck_job_runs_job")
    op.execute(f'ALTER TABLE job_runs ADD CONSTRAINT ck_job_runs_job CHECK ("job" IN {_JOBS_OLD})')
    op.drop_index("ix_roles_user_seen", table_name="roles")
    op.drop_index("ix_scan_runs_started", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_table("profile_documents")
    op.drop_index("ix_role_pool_user_waiting", table_name="role_pool")
    op.drop_table("role_pool")
