"""The card's current GitHub branch: `dev_tasks.pr_branch` (CODE_HOST=github).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-28

A fresh dev run pushes a new branch, `agent/task-<id>-<run8>`; rework runs push to the card's
`pr_branch`, so its PR updates. Nothing is force-pushed. PR sync follows this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dev_tasks", sa.Column("pr_branch", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dev_tasks", "pr_branch")
