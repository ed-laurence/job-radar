"""Feed replay scores the roles it releases through the jobs area's `RoleScorer` (composed in
`app.compose.jobs`), on the real LLM adapter replaying the recorded score fixture."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.adapters.fakes.system import FakeClock
from app.compose.jobs import build_services, job_stores, role_scorer
from app.domain.errors import Forbidden
from app.domain.identity import AppRole, Principal, derive_user_id
from app.domain.jobs.roles import RoleDraft
from app.jobs import feed_replay
from app.main import Container
from tests.conftest import MAINTAINER

AD = (
    "Acme Pty Ltd is hiring a Senior Data Engineer in Melbourne VIC, hybrid, $150k-$170k. "
    "You'll build pipelines in Python and SQL. Contact Sam Lee, Talent Partner."
)


async def test_released_roles_are_scored(container: Container, clock: FakeClock) -> None:
    owner = Principal(user_id=derive_user_id(MAINTAINER), email=MAINTAINER, role=AppRole.MAINTAINER)
    stores = job_stores(container)
    services = build_services(container, stores)
    role = await services.roles.create_role(
        owner, RoleDraft(title="Senior Data Engineer", company="Acme Pty Ltd", description=AD)
    )
    released = feed_replay.ReleasedRole(owner.user_id, role.id)
    held = feed_replay.InMemoryHeldBackRoles(held=[(released, clock.now() - timedelta(hours=1))])
    report = await feed_replay.run(container, roles=held, scorer=role_scorer(container, stores))
    assert report.ok
    assert "Scored 1 roles." in report.notes
    scored = await services.roles.get_role(owner, role.id)
    assert scored.stage == "scored"
    assert scored.score == 79


async def test_the_scorer_acts_only_for_allowlisted_owners(container: Container) -> None:
    scorer = role_scorer(container)
    stranger = derive_user_id("stranger@example.test")
    with pytest.raises(Forbidden):
        await scorer.score_role(stranger, stranger)
