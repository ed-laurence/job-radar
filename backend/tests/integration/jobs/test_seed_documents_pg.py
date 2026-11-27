"""The dev seed gives the owner the demo CVs (fictional Alex Example) as profile documents, in
the `profile_documents` manifest; the tester starts empty and a re-seed adds nothing.

Regression: the seed's owner URL skips RLS, and the manifest read used to rely on RLS alone, so
the tester saw the owner's manifest."""

from __future__ import annotations

import json

from pydantic import SecretStr

from app.adapters.db.engine import create_engine, session_factory
from app.adapters.db.profile_documents import PgManifests
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, InMemoryStorage, SequentialIds
from app.adapters.pdf import PypdfText
from app.domain.identity import AppRole, Principal, derive_user_id
from app.domain.jobs.documents import ProfileDocumentService
from app.jobs.seed import load_seed
from app.settings import Settings
from tests.integration.conftest import Database

OWNER = "docs-owner@example.test"
TESTER = "docs-tester@example.test"


async def test_owner_gets_the_demo_cvs_once_and_the_tester_none(database: Database) -> None:
    users = json.dumps([{"email": OWNER, "role": "maintainer"}, {"email": TESTER, "role": "user"}])
    settings = Settings(environment="test", app_users=SecretStr(users))
    engine = create_engine(database.owner_url, pool_size=1)
    try:
        documents = ProfileDocumentService(
            InMemoryStorage(),
            PypdfText(),
            FakeContentGuard(),
            FakePII(),
            FakeClock(),
            SequentialIds(),
            PgManifests(session_factory(engine)),
        )
        await load_seed(settings, engine, FakeClock(), documents)
        again = await load_seed(settings, engine, FakeClock(), documents)
        owner = Principal(derive_user_id(OWNER), OWNER, AppRole.MAINTAINER)
        tester = Principal(derive_user_id(TESTER), TESTER, AppRole.USER)
        slots = [d.slot for d in await documents.list_documents(owner)]
        assert slots == ["technical", "leadership"]
        assert await documents.list_documents(tester) == []
        assert again == {OWNER: 0, TESTER: 0}
    finally:
        await engine.dispose()
