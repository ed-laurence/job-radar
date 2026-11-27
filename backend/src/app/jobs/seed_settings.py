"""The owner's seeded settings (`seed/owner-settings.json`): "Your extra skills" for the
fictional Alex Example, things the demo CVs don't already say. Set only while the owner's
skills are empty, so a re-seed (or the user's own text) is never overwritten. The file is
under `seed/`, so the PII gate (`build_seed.py --verify`) scans it."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.engine import user_transaction

OWNER_SETTINGS = "owner-settings.json"
_SET_SKILLS = text(
    """
    UPDATE user_settings
    SET settings = jsonb_set(
            settings,
            '{profile}',
            COALESCE(settings -> 'profile', '{}'::jsonb)
                || jsonb_build_object('skills', CAST(:skills AS text)),
            true
        ),
        version = version + 1,
        updated_at = now()
    WHERE user_id = :user_id
      AND btrim(COALESCE(settings #>> '{profile,skills}', '')) = ''
    """
)


def owner_skills(seed_dir: Path) -> str | None:
    """The seeded extra skills, one per line; None when the file is absent or has none."""
    path = seed_dir / OWNER_SETTINGS
    if not path.is_file():
        return None
    raw = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    profile = raw.get("profile")
    lines = cast("dict[str, object]", profile).get("skills") if isinstance(profile, dict) else None
    if not isinstance(lines, list):
        return None
    found = [line.strip() for line in cast("list[object]", lines) if isinstance(line, str)]
    return "\n".join(line for line in found if line) or None


async def seed_skills(
    sessions: async_sessionmaker[AsyncSession], user_id: uuid.UUID, skills: str
) -> int:
    """Set the user's extra skills if they're empty. Returns 1 when written, else 0."""
    async with user_transaction(sessions, user_id) as session:
        result = await session.execute(_SET_SKILLS, {"skills": skills, "user_id": user_id})
    return max(0, int(getattr(result, "rowcount", 0) or 0))
