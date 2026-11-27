"""Campaign week maths, plan parsing, actual counts, evidence, follow-ups due and archive."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.adapters.campaign_plan import YamlPlanSource
from app.adapters.fakes.jobs import MemoryRoles, MemoryRows
from app.adapters.fakes.jobs_campaign import MemoryCampaign
from app.adapters.fakes.security import FakeContentGuard
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.domain.errors import NotFound, ValidationFailed
from app.domain.identity import AppRole, Principal
from app.domain.jobs.campaign import (
    METRICS,
    ActivityDraft,
    CampaignService,
    clamp_week,
    count_actuals,
    monday,
    parse_plan,
    plan_start,
    targets_for,
    task_weeks,
    week_bounds,
    week_index,
)
from app.domain.jobs.evidence import role_evidence
from app.domain.jobs.main_page import MainPageService, reason_status
from app.domain.jobs.ports import CampaignFacts, RoleFact
from app.domain.jobs.records import ContactRecord, RejectionSummaryRecord
from app.domain.jobs.roles import RoleDraft, RoleService, StatusChange
from app.domain.jobs.summary import followups_due
from app.domain.ports.health import JobRunFact, SiteUsage, UsageCallFact
from app.settings import Settings

NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)  # a Wednesday
ME = Principal(user_id=uuid.uuid4(), email="me@example.test", role=AppRole.USER)
OTHER = Principal(user_id=uuid.uuid4(), email="other@example.test", role=AppRole.USER)
PLAN: dict[str, object] = {
    "name": "Test plan",
    "start_offset_days": -37,
    "weeks": 7,
    "targets": {"applications": 4, "invites": 10},
    "week_targets": {2: {"applications": 1}},
    "checkpoints": [{"week": 4, "text": "Halfway."}, {"week": 2, "text": "Early."}],
    "tasks": [
        {"id": "every", "title": "Every week", "weeks": "all", "metric": "invites"},
        {"id": "early", "title": "Early", "weeks": [1, 2], "steps": "1. Do it", "links": []},
        {"id": "late", "title": "Late", "weeks": "5-7"},
    ],
}


class _Plan:
    def load(self) -> dict[str, object]:
        return PLAN


# ----- week maths -----


def test_monday_and_week_index() -> None:
    start = date(2026, 10, 19)
    assert monday(date(2026, 10, 25)) == start
    assert monday(start) == start
    assert week_index(start, start) == 1
    assert week_index(start + timedelta(days=6), start) == 1
    assert week_index(start + timedelta(days=7), start) == 2
    assert week_index(start - timedelta(days=1), start) == 0
    assert week_bounds(start, 2) == (date(2026, 10, 26), date(2026, 11, 1))


def test_clamp_week_defaults_to_current_and_stays_in_the_plan() -> None:
    assert clamp_week(None, 3, 7) == 3
    assert clamp_week(None, -2, 7) == 1
    assert clamp_week(None, 12, 7) == 7
    assert clamp_week(9, 3, 7) == 7


def test_task_weeks_forms() -> None:
    assert task_weeks("all", 3) == {1, 2, 3}
    assert task_weeks(None, 2) == {1, 2}
    assert task_weeks([1, 3], 7) == {1, 3}
    assert task_weeks("2-4", 7) == {2, 3, 4}
    assert task_weeks(5, 7) == {5}


def test_plan_start_is_relative_to_the_anchor_and_snaps_to_monday() -> None:
    plan = parse_plan(PLAN)
    assert plan_start(plan, NOW.date()) == date(2026, 10, 19)
    explicit = parse_plan({**PLAN, "start": "2026-11-04"})
    assert plan_start(explicit, NOW.date()) == date(2026, 11, 2)


def test_week_targets_override_the_defaults() -> None:
    plan = parse_plan(PLAN)
    assert targets_for(plan, 1)["applications"] == 4
    assert targets_for(plan, 2) == {"applications": 1, "invites": 10}


def test_parse_rejects_unknown_metrics() -> None:
    with pytest.raises(ValueError, match="unknown metrics"):
        parse_plan({**PLAN, "targets": {"bogus": 1}})


def test_the_repo_plan_parses() -> None:
    plan = parse_plan(YamlPlanSource(Settings(environment="test").campaign_config).load())
    assert plan.weeks == 7
    assert 3 <= len(plan.checkpoints) <= 4
    assert len(plan.tasks) >= 8
    assert all(
        url.startswith("https://") and ".example.test/" in url
        for t in plan.tasks
        for _, url in t.links
    )


# ----- actuals -----


def test_count_actuals_maps_kinds_and_adds_automatic_counts() -> None:
    day = date(2026, 11, 24)
    facts = CampaignFacts(
        activity=(
            (day, "agency_call"),
            (day, "linkedin_invite"),
            (day, "linkedin_invite"),
            (day, "note"),
        ),
        applied=(NOW, NOW),
        followups=(NOW,),
    )
    counts = count_actuals(facts)
    assert set(counts) == {m.key for m in METRICS}
    assert counts["agency_calls"] == 1
    assert counts["invites"] == 2
    assert counts["applications"] == 2
    assert counts["followups"] == 1
    assert counts["screens"] == 0


# ----- the service over in-memory stores -----


def _service() -> tuple[CampaignService, RoleService, MemoryRoles]:
    clock, ids = FakeClock(NOW), SequentialIds()
    roles = MemoryRoles()
    store = MemoryCampaign(roles, MemoryRows(ContactRecord, "url"))
    role_service = RoleService(roles, FakeContentGuard(), clock, ids)
    return CampaignService(_Plan(), store, role_service, clock, ids), role_service, roles


async def test_plan_counts_this_weeks_activity_and_applications() -> None:
    service, role_service, _ = _service()
    role = await role_service.create_role(ME, RoleDraft(title="Engineer"))
    await role_service.change_status(ME, role.id, StatusChange(status="applied", version=1))
    await service.log_activity(ME, ActivityDraft(on_date=NOW.date(), kind="linkedin_invite"))
    last_week = NOW.date() - timedelta(days=7)
    await service.log_activity(ME, ActivityDraft(on_date=last_week, kind="linkedin_invite"))
    view = await service.plan(ME, None)
    assert (view.current_week, view.week, view.total_weeks) == (6, 6, 7)
    assert view.week_start == date(2026, 11, 23)
    metrics = {m.key: (m.actual, m.target) for m in view.metrics}
    assert metrics["invites"] == (1, 10)
    assert metrics["applications"] == (1, 4)
    assert view.checkpoint is None
    early = await service.plan(ME, 1)
    assert early.checkpoint is not None
    assert early.checkpoint.week == 2
    assert early.checkpoint.due == date(2026, 11, 1)
    other = await service.plan(OTHER, None)
    assert all(m.actual == 0 for m in other.metrics)


async def test_tasks_for_a_week_with_done_and_progress() -> None:
    service, _, _ = _service()
    week1 = await service.tasks(ME, 1)
    assert [t.id for t in week1.items] == ["every", "early"]
    assert week1.items[0].progress is not None
    assert week1.items[1].progress is None
    await service.set_task_done(ME, "early", 1, done=True)
    await service.set_task_done(ME, "early", 1, done=True)
    assert [t.done for t in (await service.tasks(ME, 1)).items] == [False, True]
    assert not any(t.done for t in (await service.tasks(OTHER, 1)).items)
    await service.set_task_done(ME, "early", 1, done=False)
    assert not any(t.done for t in (await service.tasks(ME, 1)).items)
    with pytest.raises(NotFound):
        await service.set_task_done(ME, "late", 1, done=True)


async def test_activity_log_validation_and_delete() -> None:
    service, _, _ = _service()
    with pytest.raises(ValidationFailed):
        await service.log_activity(ME, ActivityDraft(on_date=NOW.date(), kind="bogus"))
    with pytest.raises(ValidationFailed):
        future = NOW.date() + timedelta(days=5)
        await service.log_activity(ME, ActivityDraft(on_date=future, kind="screen"))
    with pytest.raises(ValidationFailed):
        draft = ActivityDraft(on_date=NOW.date(), kind="screen", role_id=uuid.uuid4())
        await service.log_activity(ME, draft)
    logged = await service.log_activity(
        ME, ActivityDraft(on_date=NOW.date(), kind="screen", who="  Sam  ", notes=" ")
    )
    assert (logged.who, logged.notes) == ("Sam", None)
    assert [a.id for a in (await service.activity(ME, None)).items] == [logged.id]
    with pytest.raises(NotFound):
        await service.delete_activity(OTHER, logged.id)
    await service.delete_activity(ME, logged.id)
    assert (await service.activity(ME, None)).items == ()


# ----- archive -----


async def test_archive_moves_old_act_now_roles_to_closed_with_a_new_version() -> None:
    _, role_service, roles = _service()
    old = await role_service.create_role(
        ME, RoleDraft(title="Old", posted_at=NOW - timedelta(days=20))
    )
    fresh = await role_service.create_role(
        ME, RoleDraft(title="Fresh", posted_at=NOW - timedelta(days=2))
    )
    applied = await role_service.create_role(
        ME, RoleDraft(title="Applied", status="applied", posted_at=NOW - timedelta(days=30))
    )
    mine = await role_service.create_role(
        OTHER, RoleDraft(title="Not mine", posted_at=NOW - timedelta(days=30))
    )
    assert await role_service.archive_older(ME, 14) == 1
    after = await role_service.get_role(ME, old.id)
    assert (after.status, after.version) == ("closed", old.version + 1)
    assert (await role_service.get_role(ME, fresh.id)).status == "new"
    assert (await role_service.get_role(ME, applied.id)).status == "applied"
    assert (await role_service.get_role(OTHER, mine.id)).status == "new"
    kinds = [(e.kind, e.to_value) for (_, _), e in roles.events.items() if e.role_id == old.id]
    assert ("status", "closed") in kinds
    assert await role_service.archive_older(ME, 14) == 0
    with pytest.raises(ValidationFailed):
        await role_service.archive_older(ME, 0)


# ----- evidence, follow-ups due, reason status -----


def test_evidence_from_scoring_items() -> None:
    items = [
        {"requirement": "Python", "match": "shown", "source": "cv"},
        {"requirement": "Spark", "match": "shown", "source": "cv"},
        {"requirement": "dbt", "match": "shown", "source": "cv"},
        {"requirement": "Airflow", "match": "shown", "source": "records"},
        {"requirement": "Scala", "match": "partial", "source": "none"},
    ]
    found = role_evidence(78, {"skills": 40, "evidence": items})
    assert found is not None
    assert (found.must_haves_total, found.shown, found.from_records) == (5, 4, 1)
    assert found.verdict == "apply"
    assert found.summary == (
        "Apply · you show 4 of 5 must-haves (1 from your records, not on your CV)"
    )
    assert [i.match for i in found.items] == ["shown"] * 4 + ["partial"]


def test_evidence_estimated_from_skills_and_absent_without_breakdown() -> None:
    estimated = role_evidence(40, {"skills": 20, "track_pay": 10})
    assert estimated is not None
    assert (estimated.must_haves_total, estimated.shown, estimated.verdict) == (5, 2, "skip")
    assert estimated.items == ()
    assert role_evidence(80, None) is None
    assert role_evidence(80, {"location": 15}) is None


def test_followups_due_needs_a_day_and_no_follow_up() -> None:
    def fact(applied: datetime | None, followed: datetime | None = None) -> RoleFact:
        return RoleFact(
            id=uuid.uuid4(),
            status="applied",
            found_at=NOW - timedelta(days=9),
            applied_at=applied,
            followed_up_at=followed,
        )

    roles = [
        fact(NOW - timedelta(days=3)),
        fact(NOW - timedelta(hours=3)),
        fact(NOW - timedelta(days=3), NOW),
        fact(None),
    ]
    assert followups_due(roles, NOW) == 1


def test_reason_status() -> None:
    base = RejectionSummaryRecord(role_id=uuid.uuid4(), title="Role")
    assert reason_status(base) == "missing"
    pasted = RejectionSummaryRecord(role_id=base.role_id, title="R", has_text=True)
    assert reason_status(pasted) == "pasted"
    analysed = RejectionSummaryRecord(role_id=base.role_id, title="R", categories=("pay",))
    assert reason_status(analysed) == "analysed"


class _Health:
    def __init__(self, runs: list[JobRunFact]) -> None:
        self._runs = runs

    async def last_job_runs(self, jobs: tuple[str, ...]) -> list[JobRunFact]:
        return [run for run in self._runs if run.job in jobs]

    async def llm_calls(self, user_id: uuid.UUID, start: datetime) -> list[UsageCallFact]:
        del user_id, start
        return []

    async def site_usage(self, start: datetime) -> SiteUsage:
        del start
        return SiteUsage(page_views=0, sessions=0, vitals_p75=())


async def test_alerts_for_failed_jobs_followups_and_a_quiet_feed() -> None:
    roles = MemoryRoles()
    store = MemoryCampaign(roles, MemoryRows(ContactRecord, "url"))
    clock, ids = FakeClock(NOW), SequentialIds()
    role_service = RoleService(roles, FakeContentGuard(), clock, ids)
    role = await role_service.create_role(ME, RoleDraft(title="Applied", status="applied"))
    clock.advance(timedelta(days=2))
    runs = [
        JobRunFact(job="digest", started_at=NOW, ok=False),
        JobRunFact(job="scan", started_at=NOW + timedelta(days=1), ok=True),
    ]
    alerts = await MainPageService(_Health(runs), store, clock).alerts(ME)
    assert [(a.level, a.title) for a in alerts] == [
        ("error", "The daily digest failed"),
        ("warning", "1 application to follow up"),
        ("info", "No new roles from the feed"),
    ]
    assert all("digest" not in a.text and "_" not in a.text for a in alerts)
    assert role.applied_at is not None
    assert await MainPageService(_Health([]), store, clock).alerts(OTHER) == []
