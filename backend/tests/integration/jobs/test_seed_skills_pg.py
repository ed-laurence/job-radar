"""The dev seed sets the owner's "Your extra skills" (fictional Alex Example) only while they're
empty: the tester gets none, a re-seed writes nothing, and the user's own text is kept."""

from __future__ import annotations

import json
from dataclasses import replace

from pydantic import SecretStr

from app.adapters.db.engine import create_engine, session_factory
from app.adapters.db.user_settings import PgUserSettings
from app.adapters.fakes.system import FakeClock
from app.domain.identity import AppRole, Principal, derive_user_id
from app.domain.user_settings import SettingsService
from app.jobs.seed import load_seed
from app.jobs.seed_settings import owner_skills, seed_skills
from app.settings import Settings
from tests.integration.conftest import Database

OWNER = "skills-owner@example.test"
TESTER = "skills-tester@example.test"


async def test_owner_skills_are_seeded_once_and_never_overwrite(database: Database) -> None:
    users = json.dumps([{"email": OWNER, "role": "maintainer"}, {"email": TESTER, "role": "user"}])
    settings = Settings(environment="test", app_users=SecretStr(users))
    engine = create_engine(database.owner_url, pool_size=1)
    try:
        sessions = session_factory(engine)
        service = SettingsService(PgUserSettings(sessions))
        owner = Principal(derive_user_id(OWNER), OWNER, AppRole.MAINTAINER)
        tester = Principal(derive_user_id(TESTER), TESTER, AppRole.USER)
        seeded = owner_skills(settings.seed_dir)
        assert seeded is not None

        await load_seed(settings, engine, FakeClock())
        mine = await service.get(owner)
        assert mine.skills == seeded
        assert mine.version == 2  # the seeded row, then the skills
        assert (await service.get(tester)).skills == ""

        again = await load_seed(settings, engine, FakeClock())
        assert again == {OWNER: 0, TESTER: 0}

        await service.update(owner, replace(mine, skills="My own text"))
        assert await seed_skills(sessions, owner.user_id, seeded) == 0
        assert (await service.get(owner)).skills == "My own text"
    finally:
        await engine.dispose()
