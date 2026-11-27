"""Rejection analysis and follow-up drafts through the real LLM adapter (routing, guards, PII,
`llm_calls` logging) replaying recorded fixtures. Both run on the open-model route."""

from __future__ import annotations

import pytest

from app.adapters.fakes.data import InMemoryUsageStore
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.llm.providers.replay import ReplayProvider
from app.adapters.llm.routing import load_routing
from app.adapters.llm.service import LlmService
from app.domain.jobs.followups import FollowupService
from app.domain.jobs.rejections import RejectionService
from app.domain.jobs.roles import RoleDraft, StatusChange
from app.domain.ports import LlmFeature
from app.settings import Settings
from tests.unit.jobs.support import ALICE, Domain

AD = "Acme Pty Ltd needs a Senior Data Engineer. Questions to Sam Lee, Talent Partner."


@pytest.fixture
def usage() -> InMemoryUsageStore:
    return InMemoryUsageStore()


@pytest.fixture
def domain(settings: Settings, usage: InMemoryUsageStore) -> Domain:
    found = Domain()
    replay = ReplayProvider(settings.llm_fixtures_dir / "jobs")
    llm = LlmService(
        load_routing(settings.models_config),
        dict.fromkeys(("vertex-claude", "vertex-maas", "vertex-gemini"), replay),
        FakeContentGuard(),
        FakePII(),
        usage,
        found.clock,
    )
    found.rejections = RejectionService(found.roles, found.roles_store, llm, found.clock)
    found.followups = FollowupService(
        found.roles, found.contacts_store, found.followups_store, llm, found.ids
    )
    return found


async def test_rejection_analysis_replays_on_the_open_model_route(
    domain: Domain, usage: InMemoryUsageStore
) -> None:
    role = await domain.roles.create_role(ALICE, RoleDraft(title="Senior Data Engineer"))
    await domain.roles.change_status(ALICE, role.id, StatusChange(status="rejected", version=1))
    await domain.roles_store.update_rejection(
        ALICE.user_id,
        role.id,
        {"rejection_text": "We went with a candidate with more hands-on Spark experience."},
    )
    result = await domain.rejections.analyse(ALICE, role.id)
    assert result.reason_categories == ("skills_gap",)
    call = usage.calls[-1]
    assert call.feature is LlmFeature.REJECTION_ANALYSIS
    assert call.is_ok
    assert call.role_id == role.id


async def test_followup_drafts_replay(domain: Domain, usage: InMemoryUsageStore) -> None:
    role = await domain.roles.create_role(
        ALICE, RoleDraft(title="Senior Data Engineer", company="Acme Pty Ltd", description=AD)
    )
    await domain.roles.change_status(ALICE, role.id, StatusChange(status="applied", version=1))
    drafts = await domain.followups.draft(ALICE, role.id)
    assert [(d.source, d.target_name) for d in drafts] == [("ad", "Sam Lee")]
    assert drafts[0].problems == ()
    assert usage.calls[-1].feature is LlmFeature.FOLLOWUP
