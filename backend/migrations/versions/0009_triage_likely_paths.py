"""Triage keeps the repo paths it expects the change to touch: `task_triage.likely_paths`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-28

They decide risk and scope at triage time, and they are already checked against the repo, so
plan runs get them as a starting point instead of searching from scratch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_triage",
        sa.Column(
            "likely_paths",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("task_triage", "likely_paths")
