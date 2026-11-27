"""The real seed splits into 300 visible roles (every role with history first, then the newest
Act now roles) and a hidden pool, the same way every time."""

from __future__ import annotations

from app.jobs.seed_context import read_seed
from app.jobs.seed_pool import VISIBLE_ROLES, visible_role_ids
from app.settings import Settings


def test_three_hundred_visible_roles_with_all_history_kept() -> None:
    _, data = read_seed(Settings(environment="test").seed_dir)
    visible = visible_role_ids(data)
    assert len(visible) == VISIBLE_ROLES == 300
    assert visible == visible_role_ids(data)
    roles = {int(r["id"]): r for r in data["roles"]}
    applied = {i for i, r in roles.items() if r["status"] in {"applied", "unknown", "rejected"}}
    assert applied <= visible
    for name in ("chats", "followups", "rejections", "role_recruiters"):
        assert {int(row["role_id"]) for row in data[name]} <= visible, name
    act_now = [r for r in roles.values() if r["status"] == "new" and r.get("stage") != "filtered"]
    assert {int(r["id"]) for r in act_now} <= visible
    assert len(roles) - len(visible) == 1219
