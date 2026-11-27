"""Triage verifies, prioritises and routes; the planner writes the acceptance criteria.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-28

- `dev_tasks` and `task_triage` gain `priority` (P0-P3), `severity` and `route`
  (now/next/later); task types add `security` and `performance`.
- `task_triage` gains `impact`, `route_reason` and `verification` (JSON: status, evidence,
  duplicate_of, code_refs). Its `acceptance_criteria` column stays for older rows only.
- `task_plans.acceptance_criteria`: the planner's criteria (test id + expectation).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = ("feature", "bug", "chore", "refactor", "test", "docs")
NEW_TYPES = ("feature", "bug", "security", "performance", "chore", "refactor", "test", "docs")
CHECKS = {
    "priority": ("P0", "P1", "P2", "P3"),
    "severity": ("critical", "major", "minor", "trivial"),
    "route": ("now", "next", "later"),
}


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f'"{column}" IS NULL OR "{column}" IN ({quoted})'


def _set_types(table: str, values: tuple[str, ...]) -> None:
    name = f"ck_{table}_type"
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, _in("type", values))


def upgrade() -> None:
    for table in ("dev_tasks", "task_triage"):
        _set_types(table, NEW_TYPES)
        for column, values in CHECKS.items():
            op.add_column(table, sa.Column(column, sa.Text(), nullable=True))
            op.create_check_constraint(op.f(f"ck_{table}_{column}"), table, _in(column, values))
    op.add_column("task_triage", sa.Column("impact", sa.Text(), nullable=True))
    op.add_column("task_triage", sa.Column("route_reason", sa.Text(), nullable=True))
    op.add_column(
        "task_triage",
        sa.Column("verification", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_plans",
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("task_plans", "acceptance_criteria")
    for column in ("verification", "route_reason", "impact"):
        op.drop_column("task_triage", column)
    for table in ("dev_tasks", "task_triage"):
        for column in CHECKS:
            op.drop_column(table, column)
        op.execute(
            sa.text(
                f"UPDATE {table} SET type = 'feature' WHERE type IN ('security', 'performance')"
            )
        )
        _set_types(table, OLD_TYPES)
