"""Agent review: `task_reviews`, and the card's branch, base and review round.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-28

See docs/features/agent-review.md. A review run's verdict and findings are stored per round;
the card keeps its latest published agent branch and how many review rounds it has had.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERDICTS = "'approved', 'changes_requested', 'needs_decision'"
APP_ROLE = "jobradar_app"
# After a transaction-local set_config ends, the setting reads '' (not NULL), hence NULLIF.
_CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    # Human Request changes add review rounds beyond the agent cap of 2.
    op.drop_constraint(op.f("ck_runs_round"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_round"), "runs", '"round" >= 0 AND "round" <= 20')
    op.add_column("dev_tasks", sa.Column("branch", sa.Text(), nullable=True))
    op.add_column("dev_tasks", sa.Column("base_sha", sa.Text(), nullable=True))
    op.add_column(
        "dev_tasks",
        sa.Column("review_round", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "task_reviews",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f'"verdict" IN ({VERDICTS})', name=op.f("ck_task_reviews_verdict")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["dev_tasks.id"],
            name=op.f("fk_task_reviews_task_id_dev_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_task_reviews_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_reviews")),
    )
    op.create_index(
        "ix_task_reviews_task_created", "task_reviews", ["task_id", "created_at"], unique=False
    )
    # Shared like the rest of the board: any signed-in account reads; writes are domain-checked.
    op.execute("ALTER TABLE task_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY signed_in ON task_reviews FOR ALL "
        f"USING ({_CURRENT_USER} IS NOT NULL) WITH CHECK ({_CURRENT_USER} IS NOT NULL)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON task_reviews TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_task_reviews_task_created", table_name="task_reviews")
    op.drop_table("task_reviews")
    op.drop_column("dev_tasks", "review_round")
    op.drop_column("dev_tasks", "base_sha")
    op.drop_column("dev_tasks", "branch")
    op.execute("UPDATE runs SET round = 2 WHERE round > 2")
    op.drop_constraint(op.f("ck_runs_round"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_round"), "runs", '"round" >= 0 AND "round" <= 2')
