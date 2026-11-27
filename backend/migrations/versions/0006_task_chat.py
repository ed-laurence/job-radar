"""Task chat: `task_chat_proposals`, `task_chat_messages`, and the `task_chat` LLM feature.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27

- `task_chat_proposals` (user-owned, RLS FORCEd): edits Claude proposed for a dev-board card
  (field diffs as JSON), pending until the account that asked accepts or rejects them.
- `task_chat_messages` (user-owned, RLS FORCEd): each account's own conversation about a shared
  card. An assistant message points at the proposal it made, if any.
- `llm_calls.feature` and `llm_budget_usage.feature` accept `task_chat`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "jobradar_app"
_CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_STAMP = sa.DateTime(timezone=True)
_FEATURES_OLD = (
    "('score', 'extract', 'kit', 'chat', 'research', 'triage', 'plan', 'dev', 'review', "
    "'digest', 'followup', 'outreach', 'rejection_analysis', 'eval', 'evidence')"
)
_FEATURES_NEW = _FEATURES_OLD[:-1] + ", 'task_chat')"
_FEATURE_CHECKS = (
    ("llm_calls", "ck_llm_calls_feature"),
    ("llm_budget_usage", "ck_llm_budget_usage_feature"),
)
_TABLES = ("task_chat_proposals", "task_chat_messages")


def _features(values: str) -> None:
    for table, name in _FEATURE_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ("feature" IN {values})')


def _owned(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY owner ON {table} FOR ALL "
        f"USING (user_id = {_CURRENT_USER}) WITH CHECK (user_id = {_CURRENT_USER})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def upgrade() -> None:
    op.create_table(
        "task_chat_proposals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("decided_at", _STAMP, nullable=True),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "\"status\" IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_task_chat_proposals_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_chat_proposals_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["dev_tasks.id"],
            name=op.f("fk_task_chat_proposals_task_id_dev_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_chat_proposals")),
    )
    op.create_index(
        "ix_task_chat_proposals_task_created",
        "task_chat_proposals",
        ["task_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "task_chat_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "\"author\" IN ('user', 'assistant')", name=op.f("ck_task_chat_messages_author")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_chat_messages_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["dev_tasks.id"],
            name=op.f("fk_task_chat_messages_task_id_dev_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["task_chat_proposals.id"],
            name=op.f("fk_task_chat_messages_proposal_id_task_chat_proposals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_chat_messages")),
    )
    op.create_index(
        "ix_task_chat_messages_task_created",
        "task_chat_messages",
        ["task_id", "created_at"],
        unique=False,
    )
    for table in _TABLES:
        _owned(table)
    _features(_FEATURES_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM llm_calls WHERE feature = 'task_chat'")
    op.execute("DELETE FROM llm_budget_usage WHERE feature = 'task_chat'")
    _features(_FEATURES_OLD)
    op.drop_index("ix_task_chat_messages_task_created", table_name="task_chat_messages")
    op.drop_table("task_chat_messages")
    op.drop_index("ix_task_chat_proposals_task_created", table_name="task_chat_proposals")
    op.drop_table("task_chat_proposals")
