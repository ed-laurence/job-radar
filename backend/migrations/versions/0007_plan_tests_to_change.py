"""Plans list the existing tests they have to edit: `task_plans.tests_to_change`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27

The sandboxed planner names existing test files the change must edit. Existing tests are
read-only for agents unless a maintainer adds a `modify-tests:` label, so plan review shows
this list and approving it can add the label.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_plans",
        sa.Column(
            "tests_to_change",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("task_plans", "tests_to_change")
