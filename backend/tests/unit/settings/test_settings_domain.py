"""Settings in the domain: scoring thresholds, the candidate profile in prompts, kit spelling,
the archive default, the monthly spend cap, the health budget and the insights metrics."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.adapters.fakes.data import InMemoryUsageStore
from app.adapters.fakes.health import MemoryHealth
from app.adapters.fakes.system import FakeClock
from app.adapters.fakes.user_settings import MemoryUserSettings
from app.domain.errors import BudgetExceeded
from app.domain.health import HealthService
from app.domain.identity import Principal
from app.domain.jobs.insights import job_insights, score_bands
from app.domain.jobs.kit_checks import kit_problems
from app.domain.jobs.metrics import DateRange
from app.domain.jobs.ports import RoleFact, StatusFact
from app.domain.jobs.profiles import ProfileWithSettings
from app.domain.jobs.prompts import profile_block
from app.domain.jobs.records import CandidateProfile
from app.domain.jobs.roles import RoleDraft, RoleService
from app.domain.jobs.scoring import Assessment, Breakdown, ScoringService, scored_values
from app.domain.money import to_aud
from app.domain.ports import LlmCallRecord, LlmFeature
from app.domain.user_settings import (
    DEFAULTS,
    FollowupPrefs,
    KitPrefs,
    MonthlySpendCap,
    ScoringPrefs,
    SettingsService,
)
from tests.unit.jobs.support import ALICE, BOB, Domain

NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)


def _assessment(*, mission: str | None = None) -> Assessment:
    return Assessment(
        track="contract",
        mission=mission,
        breakdown=Breakdown(skills=30, track_pay=10, location=8, freshness=0),
        relevant=True,
        reason="A data role.",
    )


async def _settings(clock: FakeClock, principal: Principal, **changes: object) -> SettingsService:
    service = SettingsService(MemoryUserSettings(clock))
    await service.update(principal, dataclasses.replace(DEFAULTS, **changes))
    return service


def test_the_filter_bar_follows_the_users_thresholds() -> None:
    # 30 + 10 + 8 + 4 (unknown post date) = 52.
    assert scored_values(_assessment(), None, NOW)["stage"] == "scored"
    strict = ScoringPrefs(keep_score=60, borderline_score=55, notify_score=80)
    values = scored_values(_assessment(), None, NOW, strict)
    assert values["stage"] == "filtered"
    assert values["filter_reason"] == "ad check: score below threshold"
    # A mission organisation still skips the bar.
    assert scored_values(_assessment(mission="Climate"), None, NOW, strict)["stage"] == "scored"
    # The bar is the lower of keep and borderline.
    assert ScoringPrefs(keep_score=50, borderline_score=40).filter_below == 40
    assert DEFAULTS.scoring.filter_below == 45


async def test_scoring_uses_the_saved_thresholds_and_profile() -> None:
    domain = Domain()
    prefs = await _settings(
        domain.clock,
        ALICE,
        scoring=ScoringPrefs(keep_score=60, borderline_score=55),
        skills="Python and SQL pipelines",
        brief="Remote data roles",
    )
    profiles = ProfileWithSettings(domain.profiles, prefs)
    scoring = ScoringService(
        domain.roles_store, domain.roles, profiles, domain.llm, domain.clock, domain.ids, prefs
    )
    reply = _assessment().model_dump_json()
    domain.llm.script(LlmFeature.SCORE, reply, reply)
    mine = await domain.roles.create_role(ALICE, RoleDraft(title="Data Engineer"))
    theirs = await domain.roles.create_role(BOB, RoleDraft(title="Data Engineer"))
    assert (await scoring.score_role(ALICE, mine.id)).stage == "filtered"
    assert (await scoring.score_role(BOB, theirs.id)).stage == "scored"
    alice_profile, bob_profile = (
        json.loads(next(b.text for b in r.data if b.name == "candidate_profile"))
        for r in domain.llm.requests
    )
    assert alice_profile["skills_and_experience"] == "Python and SQL pipelines"
    assert alice_profile["looking_for"] == "Remote data roles"
    assert "skills_and_experience" not in bob_profile
    assert "looking_for" not in bob_profile


async def test_an_empty_profile_leaves_the_prompt_unchanged() -> None:
    clock = FakeClock(NOW)
    base = CandidateProfile(name="Alex Example", location="Melbourne VIC")
    store = _Profiles(base)
    defaults = SettingsService(MemoryUserSettings(clock))
    unset = await ProfileWithSettings(store, defaults).load_profile(ALICE.user_id)
    assert profile_block(unset) == profile_block(base)
    blank = await _settings(clock, ALICE, skills="  ", brief="")
    overlaid = await ProfileWithSettings(store, blank).load_profile(ALICE.user_id)
    assert profile_block(overlaid) == profile_block(base)


async def test_kit_spelling_checks_follow_the_setting() -> None:
    clock = FakeClock(NOW)
    prose = {"outreach": "We optimize the organization of data."}
    for spelling, flagged in (("en-AU", True), ("en-GB", True), ("en-US", False)):
        prefs = await _settings(clock, ALICE, kits=KitPrefs(spelling=spelling))
        profile = await ProfileWithSettings(_Profiles(CandidateProfile()), prefs).load_profile(
            ALICE.user_id
        )
        assert profile.spelling == spelling
        problems = kit_problems(prose, [], profile, "")
        assert any("spelling" in p for p in problems) is flagged, spelling


async def test_archive_defaults_to_the_users_setting() -> None:
    domain = Domain()
    prefs = SettingsService(MemoryUserSettings(domain.clock))
    roles = RoleService(domain.roles_store, domain.guard, domain.clock, domain.ids, prefs)
    posted = domain.clock.now() - timedelta(days=10)
    await roles.create_role(ALICE, RoleDraft(title="Old", posted_at=posted))
    assert await roles.archive_older(ALICE) == 0  # default 14 days
    await prefs.update(ALICE, dataclasses.replace(DEFAULTS, archive_default_days=7))
    assert await roles.archive_older(ALICE) == 1
    assert await roles.archive_older(ALICE, 1) == 0  # an explicit `days` still wins


async def test_the_monthly_cap_counts_only_this_months_spend() -> None:
    clock = FakeClock(NOW)
    usage = InMemoryUsageStore()
    prefs = await _settings(clock, ALICE, monthly_budget_aud=5.0)
    cap = MonthlySpendCap(prefs, MemoryHealth(usage), usd_to_aud=1.0)
    await usage.record_call(_call(ALICE.user_id, NOW - timedelta(days=40), 9.0))
    await usage.record_call(_call(ALICE.user_id, NOW - timedelta(hours=1), 4.0))
    await cap.check(ALICE.user_id, NOW)
    await usage.record_call(_call(ALICE.user_id, NOW, 1.0))
    with pytest.raises(BudgetExceeded):
        await cap.check(ALICE.user_id, NOW)
    # No budget (the user's default without config) means no cap.
    await usage.record_call(_call(BOB.user_id, NOW, 100.0))
    await cap.check(BOB.user_id, NOW)


async def test_the_monthly_cap_compares_aud_with_converted_usd_spend() -> None:
    clock = FakeClock(NOW)
    usage = InMemoryUsageStore()
    prefs = await _settings(clock, ALICE, monthly_budget_aud=5.0)
    cap = MonthlySpendCap(prefs, MemoryHealth(usage), usd_to_aud=1.55)
    await usage.record_call(_call(ALICE.user_id, NOW, 3.0))  # 4.65 AUD: under the cap
    await cap.check(ALICE.user_id, NOW)
    await usage.record_call(_call(ALICE.user_id, NOW, 0.25))  # 5.0375 AUD: over it
    with pytest.raises(BudgetExceeded):
        await cap.check(ALICE.user_id, NOW)


async def test_a_pre_aud_budget_is_read_as_aud_and_saved_under_the_new_key() -> None:
    clock = FakeClock(NOW)
    store = MemoryUserSettings(clock)
    await store.save(ALICE.user_id, {"llm": {"monthly_budget_usd": 30.0}}, 1)
    service = SettingsService(store, default_budget=10.0)
    current = await service.get(ALICE)
    assert current.monthly_budget_aud == 30.0
    await service.update(ALICE, current)
    stored = await store.load(ALICE.user_id)
    assert stored is not None
    assert stored.data["llm"] == {"monthly_budget_aud": 30.0}


def test_to_aud_needs_a_positive_rate() -> None:
    assert to_aud(2.0, 1.55) == 3.1
    with pytest.raises(ValueError, match="greater than 0"):
        to_aud(1.0, 0)


async def test_budget_defaults_to_config_and_a_users_value_wins() -> None:
    clock = FakeClock(NOW)
    service = SettingsService(MemoryUserSettings(clock), default_budget=10.0)
    health = HealthService(MemoryHealth(), clock, 10.0, budgets=service)
    assert (await health.summary(ALICE)).llm.budget_month_aud == 10.0
    current = await service.get(ALICE)
    await service.update(ALICE, dataclasses.replace(current, monthly_budget_aud=4.0))
    assert (await health.summary(ALICE)).llm.budget_month_aud == 4.0
    # A maintainer can clear it: null means no monthly cap.
    current = await service.get(ALICE)
    await service.update(ALICE, dataclasses.replace(current, monthly_budget_aud=None))
    assert (await health.summary(ALICE)).llm.budget_month_aud is None
    # A non-maintainer's value is ignored; they keep the default.
    bob = await service.update(
        BOB, dataclasses.replace(await service.get(BOB), monthly_budget_aud=1.0)
    )
    assert bob.monthly_budget_aud == 10.0
    assert (await service.get(BOB)).monthly_budget_aud == 10.0


def test_insights_on_hand_made_facts() -> None:
    window = DateRange(date(2026, 11, 1), date(2026, 12, 1), "week")
    applied = datetime(2026, 11, 3, 9, tzinfo=UTC)

    roles = [
        RoleFact(
            id=uuid.UUID(int=1),
            status="interview",
            found_at=applied,
            applied_at=applied,
            followed_up_at=applied + timedelta(hours=20),
            score=80,
            source="seek",
            track="exec",
        ),
        RoleFact(
            id=uuid.UUID(int=2),
            status="rejected",
            found_at=applied,
            applied_at=applied + timedelta(days=7),
            score=50,
            source="seek",
        ),
        RoleFact(
            id=uuid.UUID(int=3),
            status="unknown",
            found_at=applied,
            applied_at=applied,
            followed_up_at=applied + timedelta(hours=60),
            score=30,
            source="manual",
            track="contract",
        ),
        RoleFact(id=uuid.UUID(int=4), status="new", found_at=applied, source="manual"),
    ]
    events = [
        StatusFact(role_id=uuid.UUID(int=1), at=applied + timedelta(days=2), to_value="interview"),
        StatusFact(role_id=uuid.UUID(int=2), at=applied + timedelta(days=11), to_value="rejected"),
    ]
    prefs = dataclasses.replace(DEFAULTS, followups=FollowupPrefs(after_hours=24, due_hours=48))
    view = job_insights(roles, events, window, prefs, datetime(2026, 11, 30, tzinfo=UTC))
    assert [(w.week_start, w.applied) for w in view.applications_per_week] == [
        (date(2026, 11, 2), 2),
        (date(2026, 11, 9), 1),
    ]
    assert view.response_rate == round(2 / 3, 4)
    assert view.interview_rate == round(1 / 3, 4)
    assert view.offer_rate == 0
    assert view.ghost_rate == round(1 / 3, 4)
    assert view.median_days_to_response == 3.0
    # Role 1 on time, role 3 late, role 2 overdue without a follow-up.
    assert view.followup_on_time_rate == round(1 / 3, 4)
    by_source = {b.key: b for b in view.by_source}
    assert (by_source["seek"].found, by_source["seek"].applied) == (2, 2)
    assert (by_source["seek"].interviews, by_source["seek"].rejected) == (1, 1)
    assert [b.key for b in view.by_track] == ["unknown", "contract", "exec"]
    bands = {b.key: b.found for b in view.score_bands}
    assert bands == {"0-44": 1, "45-54": 1, "55-74": 0, "75-100": 1, "unscored": 1}


def test_score_bands_follow_the_thresholds_and_empty_data_is_null() -> None:
    assert score_bands(DEFAULTS) == [(0, 44), (45, 54), (55, 74), (75, 100)]
    custom = dataclasses.replace(
        DEFAULTS, scoring=ScoringPrefs(keep_score=60, borderline_score=60, notify_score=90)
    )
    assert score_bands(custom) == [(0, 59), (60, 89), (90, 100)]
    window = DateRange(date(2026, 11, 1), date(2026, 12, 1), "week")
    empty = job_insights([], [], window, DEFAULTS, NOW)
    assert empty.applications_per_week == ()
    assert empty.response_rate is None
    assert empty.interview_rate is None
    assert empty.offer_rate is None
    assert empty.ghost_rate is None
    assert empty.median_days_to_response is None
    assert empty.followup_on_time_rate is None
    assert empty.by_source == empty.by_track == ()
    assert [b.found for b in empty.score_bands] == [0, 0, 0, 0]


class _Profiles:
    def __init__(self, profile: CandidateProfile) -> None:
        self._profile = profile

    async def load_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        del user_id
        return self._profile


def _call(user_id: uuid.UUID, at: datetime, cost: float) -> LlmCallRecord:
    return LlmCallRecord(
        user_id=user_id,
        at=at,
        feature=LlmFeature.SCORE,
        provider="replay",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cost_usd=cost,
        latency_ms=1,
        is_ok=True,
        is_fallback=False,
        guard_input="pass",
        guard_output=None,
    )
