"""Seed loading context: the seed files, the account mapping, deterministic ids and date
re-basing. See `app.jobs.seed`."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Table

from app.adapters.db.base import Base
from app.domain.identity import AppRole, derive_user_id
from app.settings import Settings

type Row = dict[str, Any]  # Any: seed JSON values of mixed types, checked by the tables.
SEED_NAMESPACE = uuid.UUID("0b8e3f2c-6d1a-5c4e-9f7b-2a1d3c5e7f90")
CHUNK = 500
SEED_ACCOUNTS = {
    1: ("owner@example.test", AppRole.MAINTAINER),
    2: ("tester@example.test", AppRole.USER),
}
WORK_MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "on_site", "on_site": "on_site"}
EVENT_KINDS = {"kit_written": "kit_regenerated", "recruiter": "note"}
LLM_FEATURES = {
    "assess": "score",
    "evidence": "score",
    "contacts": "outreach",
    "direct_message": "outreach",
    "rejection_request": "followup",
}
REJECTION_CATEGORIES = {
    "experience_gap": "experience_level",
    "domain": "experience_level",
    "skills_stack": "skills_gap",
    "leadership_scope": "seniority",
    "competition": "stronger_candidate",
    "role_changed": "role_filled",
    "fit": "culture_fit",
    "eligibility": "visa",
    "no_reason": "no_reason_given",
}
STEP_KINDS = {
    "plan": "thought",
    "test": "tool_call",
    "approval": "status",
    "claim": "lease",
    "edit": "tool_call",
    "test_run": "tool_result",
    "integrate": "tool_call",
    "publish": "message",
    "review": "finding",
    "handoff": "handoff",
}


@dataclass(frozen=True)
class Account:
    seed_id: int
    user_id: uuid.UUID
    email: str
    role: AppRole
    display_name: str | None


@dataclass
class SeedContext:
    data: Mapping[str, list[Row]]
    shift: timedelta
    account: Account
    # The manifest anchor, shifted like every other timestamp (the "now" of the seeded data).
    anchor: datetime | None = None

    def key(self, table: str, seed_id: object) -> uuid.UUID:
        return uuid.uuid5(SEED_NAMESPACE, f"{self.account.user_id}:{table}:{seed_id}")

    def ref(self, table: str, seed_id: object) -> uuid.UUID | None:
        return None if seed_id is None else self.key(table, seed_id)

    def at(self, value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")) + self.shift

    def day(self, value: object) -> date | None:
        return (date.fromisoformat(value) + self.shift) if isinstance(value, str) else None


def table_of(orm: type[Base]) -> Table:
    return cast("Table", orm.__table__)


def read_seed(seed_dir: Path) -> tuple[datetime, dict[str, list[Row]]]:
    manifest = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))
    anchor = datetime.fromisoformat(str(manifest["anchor"]).replace("Z", "+00:00"))
    data: dict[str, list[Row]] = {}
    for name in cast("dict[str, object]", manifest["files"]):
        lines = (seed_dir / name).read_text(encoding="utf-8").splitlines()
        data[name.removesuffix(".jsonl")] = [json.loads(line) for line in lines if line.strip()]
    return anchor, data


def accounts(settings: Settings, users: Sequence[Row]) -> list[Account]:
    """Seed users mapped onto `APP_USERS` by role (or kept as-is when none are configured)."""
    allowlist = settings.allowlist()
    found: list[Account] = []
    for user in users:
        seed_id = int(user["id"])
        email, role = SEED_ACCOUNTS[seed_id]
        if allowlist.entries:
            entry = allowlist.entry_for_role(role)
            if entry is None or any(account.user_id == entry.user_id for account in found):
                continue
            found.append(Account(seed_id, entry.user_id, entry.email, role, entry.display_name))
        else:
            name = user.get("display_name")
            found.append(Account(seed_id, derive_user_id(email), email, role, name))
    return found
