"""The full evidence test: `role_evidence`, and the `evidence` LLM feature.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27

- `role_evidence` (user-owned, RLS): the latest evidence test per role (must-haves, verified
  quotes, verdict, team check) as JSON. Replaced on every run.
- `llm_calls.feature` and `llm_budget_usage.feature` accept `evidence`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "jobradar_app"
_CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_STAMP = sa.DateTime(timezone=True)
_FEATURES_OLD = (
    "('score', 'extract', 'kit', 'chat', 'research', 'triage', 'plan', 'dev', 'review', "
    "'digest', 'followup', 'outreach', 'rejection_analysis', 'eval')"
)
_FEATURES_NEW = _FEATURES_OLD[:-1] + ", 'evidence')"
_FEATURE_CHECKS = (
    ("llm_calls", "ck_llm_calls_feature"),
    ("llm_budget_usage", "ck_llm_budget_usage_feature"),
)


def _features(values: str) -> None:
    for table, name in _FEATURE_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ("feature" IN {values})')


def upgrade() -> None:
    op.create_table(
        "role_evidence",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("checked_at", _STAMP, nullable=False),
        sa.Column("created_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", _STAMP, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_role_evidence_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_evidence_role_id_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_evidence")),
        sa.UniqueConstraint("role_id", name=op.f("uq_role_evidence_role_id")),
    )
    op.execute("ALTER TABLE role_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE role_evidence FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY owner ON role_evidence FOR ALL "
        f"USING (user_id = {_CURRENT_USER}) WITH CHECK (user_id = {_CURRENT_USER})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON role_evidence TO {APP_ROLE}")
    _features(_FEATURES_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM llm_calls WHERE feature = 'evidence'")
    op.execute("DELETE FROM llm_budget_usage WHERE feature = 'evidence'")
    _features(_FEATURES_OLD)
    op.drop_table("role_evidence")
