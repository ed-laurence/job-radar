"""Research steps move into the shared `run_steps` table; `research_steps` is dropped.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26

One step-stream mechanism serves dev-board and research runs. A `run_steps` row now belongs to
exactly one of a board run (`run_id`, shared: any signed-in user) or a research run
(`research_run_id`, owned: `user_id` must match `app.user_id`). Existing research steps are
copied over before the old table goes. The NOTIFY payload carries whichever run id is set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "jobradar_app"
_CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_STEP_POLICY = (
    f"CASE WHEN user_id IS NULL THEN {_CURRENT_USER} IS NOT NULL ELSE user_id = {_CURRENT_USER} END"
)
_STEP_KIND = (
    "\"kind\" IN ('thought', 'tool_call', 'tool_result', 'message', 'handoff', 'lease', "
    "'status', 'finding')"
)


def _notify_function(run_id_expression: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION notify_run_step() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('run_steps', json_build_object(
        'run_id', {run_id_expression}, 'seq', NEW.seq, 'id', NEW.id)::text);
    RETURN NEW;
END
$$"""


def upgrade() -> None:
    op.add_column("run_steps", sa.Column("research_run_id", sa.Uuid(), nullable=True))
    op.add_column("run_steps", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.alter_column("run_steps", "run_id", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key(
        op.f("fk_run_steps_research_run_id_research_runs"),
        "run_steps",
        "research_runs",
        ["research_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_run_steps_user_id_users"),
        "run_steps",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_run_steps_research_run_id_seq"), "run_steps", ["research_run_id", "seq"]
    )
    op.create_check_constraint(
        op.f("ck_run_steps_one_run"), "run_steps", "num_nonnulls(run_id, research_run_id) = 1"
    )
    op.create_check_constraint(
        op.f("ck_run_steps_research_owner"),
        "run_steps",
        "(research_run_id IS NULL) = (user_id IS NULL)",
    )
    op.execute(_notify_function("COALESCE(NEW.run_id, NEW.research_run_id)"))
    op.execute("DROP POLICY signed_in ON run_steps")
    op.execute(
        f"CREATE POLICY steps ON run_steps FOR ALL USING ({_STEP_POLICY}) "
        f"WITH CHECK ({_STEP_POLICY})"
    )
    op.execute(
        "INSERT INTO run_steps (id, research_run_id, user_id, seq, kind, agent, tool, summary,"
        " at, created_at) SELECT id, run_id, user_id, seq, kind, agent, tool, summary, at,"
        " created_at FROM research_steps"
    )
    op.drop_table("research_steps")


def downgrade() -> None:
    op.create_table(
        "research_steps",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_STEP_KIND, name=op.f("ck_research_steps_kind")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_research_steps_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_research_steps_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_steps")),
        sa.UniqueConstraint("run_id", "seq", name=op.f("uq_research_steps_run_id_seq")),
    )
    op.execute("ALTER TABLE research_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE research_steps FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY owner ON research_steps FOR ALL "
        f"USING (user_id = {_CURRENT_USER}) WITH CHECK (user_id = {_CURRENT_USER})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON research_steps TO {APP_ROLE}")
    op.execute(
        "INSERT INTO research_steps (id, user_id, run_id, seq, kind, agent, tool, summary, at,"
        " created_at) SELECT id, user_id, research_run_id, seq, kind, agent, tool, summary, at,"
        " created_at FROM run_steps WHERE research_run_id IS NOT NULL"
    )
    op.execute("DELETE FROM run_steps WHERE research_run_id IS NOT NULL")
    op.execute("DROP POLICY steps ON run_steps")
    op.execute(
        f"CREATE POLICY signed_in ON run_steps FOR ALL "
        f"USING ({_CURRENT_USER} IS NOT NULL) WITH CHECK ({_CURRENT_USER} IS NOT NULL)"
    )
    op.execute(_notify_function("NEW.run_id"))
    op.drop_constraint(op.f("ck_run_steps_research_owner"), "run_steps", type_="check")
    op.drop_constraint(op.f("ck_run_steps_one_run"), "run_steps", type_="check")
    op.drop_constraint(op.f("uq_run_steps_research_run_id_seq"), "run_steps", type_="unique")
    op.drop_constraint(op.f("fk_run_steps_user_id_users"), "run_steps", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_run_steps_research_run_id_research_runs"), "run_steps", type_="foreignkey"
    )
    op.alter_column("run_steps", "run_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("run_steps", "user_id")
    op.drop_column("run_steps", "research_run_id")
