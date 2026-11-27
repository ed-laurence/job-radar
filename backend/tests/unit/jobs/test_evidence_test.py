"""The full evidence test: verbatim-quote validation, the verdict rule, the team check, the
stored result and the role's evidence summary. Scripted LLM; fictional candidate material."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.adapters.fakes.evidence import MemoryEvidence
from app.adapters.fakes.profile import MemoryExperience
from app.domain.errors import NotFound, ValidationFailed
from app.domain.jobs.evidence import role_evidence
from app.domain.jobs.evidence_test import (
    NOT_BACKED,
    EvidenceTestService,
    MustHaveAnswer,
    Sources,
    check_item,
    derive_verdict,
    team_check,
)
from app.domain.jobs.experience import EntryText, ExperienceEntryRecord
from app.domain.jobs.records import CandidateProfile
from app.domain.jobs.roles import RoleDraft
from app.domain.ports import LlmFeature
from tests.unit.jobs.support import ALICE, BOB, Domain

CV_TECH = """Riley Example
Senior Data Engineer, Northwind Labs
- Built a streaming ingestion platform on Kafka and BigQuery
  handling 2 billion events a day.
- Cut warehouse spend by 40% by moving batch jobs to scheduled
  queries."""
CV_LEAD = "Engineering Lead, Contoso Health\n- Led a team of 8 engineers across two squads."
AT = datetime(2026, 11, 26, 7, 2, tzinfo=UTC)
RECORD = ExperienceEntryRecord(
    id=uuid.UUID(int=77),
    requirement="Observability across services",
    entries=(
        EntryText(
            context="Platform lead, Fabrikam Freight",
            detail="Ran the vendor selection for a new observability stack.",
        ),
        EntryText(
            context="Personal project",
            detail="Chose and rolled out Grafana and OpenTelemetry for 30 services.",
        ),
    ),
    version=1,
    created_at=AT,
    updated_at=AT,
)
AD = (
    "Fabrikam needs a Lead Data Engineer to run a team of 12. Must have: streaming on Kafka, "
    "BigQuery cost control, observability, dbt, and stakeholder management."
)


def _sources() -> Sources:
    profile = CandidateProfile(cv_technical=CV_TECH, cv_leadership=CV_LEAD)
    return Sources.of(profile, [RECORD])


def _answer(**fields: object) -> MustHaveAnswer:
    base: dict[str, object] = {"requirement": "Kafka streaming", "match": "shown", "why": "Ran it."}
    return MustHaveAnswer.model_validate(base | fields)


# -- verbatim quotes


def test_an_exact_cv_quote_is_kept_across_pdf_line_breaks() -> None:
    quote = (
        "Built a streaming ingestion platform on Kafka and BigQuery"
        " handling 2 billion events a day."
    )
    item = check_item(_answer(quote=quote, source="cv_technical"), _sources())
    assert (item.match, item.source, item.quote) == ("shown", "cv_technical", quote)
    assert item.record_id is None


def test_a_quote_not_in_any_text_downgrades_to_partial_without_a_quote() -> None:
    paraphrase = "Built Kafka pipelines processing billions of events."
    item = check_item(_answer(quote=paraphrase, source="cv_technical"), _sources())
    assert (item.match, item.source, item.quote) == ("partial", "none", None)
    assert item.why.startswith(NOT_BACKED)
    assert len(item.why) <= 200


def test_a_quote_from_the_other_cv_is_re_attributed() -> None:
    item = check_item(
        _answer(quote="Led a team of 8 engineers across two squads.", source="cv_technical"),
        _sources(),
    )
    assert (item.match, item.source) == ("shown", "cv_leadership")


def test_a_record_quote_carries_the_record_id_and_title() -> None:
    quote = "Chose and rolled out Grafana and OpenTelemetry for 30 services."
    item = check_item(
        _answer(requirement="Observability", quote=quote, source="records", record_id="bad-id"),
        _sources(),
    )
    assert (item.match, item.source, item.record_id) == ("shown", "records", RECORD.id)
    assert item.record_title == "Observability across services"


def test_missing_items_never_keep_a_quote_and_partial_ones_keep_only_real_quotes() -> None:
    answer = _answer(match="missing", quote="anything", source="cv_technical")
    missing = check_item(answer, _sources())
    assert (missing.match, missing.source, missing.quote) == ("missing", "none", None)
    partial = check_item(
        _answer(match="partial", quote="Cut warehouse spend by 40%", source="cv_technical"),
        _sources(),
    )
    assert (partial.match, partial.quote) == ("partial", "Cut warehouse spend by 40%")
    invented = check_item(_answer(match="partial", quote="Ran dbt", source="none"), _sources())
    assert (invented.match, invented.quote) == ("partial", None)
    assert not invented.why.startswith(NOT_BACKED)


def test_an_empty_quote_on_a_shown_item_is_not_shown() -> None:
    assert check_item(_answer(quote="   "), _sources()).match == "partial"


# -- verdict and team


@pytest.mark.parametrize(
    ("total", "shown", "score", "expected"),
    [
        (5, 5, 70, "apply"),
        (5, 4, 70, "apply"),
        (5, 3, 90, "consider"),  # attractive, but two must-haves aren't shown
        (5, 4, 69, "consider"),  # passes on evidence, under the keep threshold
        (5, 3, 40, "skip"),
        (1, 0, 80, "consider"),  # all but one of one would be nothing shown: not a pass
        (2, 1, 70, "apply"),
        (0, 0, 90, "consider"),
        (5, 5, None, "consider"),
    ],
)
def test_the_verdict_rule(total: int, shown: int, score: int | None, expected: str) -> None:
    assert derive_verdict(total, shown, score, 70) == expected


def test_a_team_over_twice_what_you_have_led_blocks_apply() -> None:
    assert derive_verdict(5, 5, 90, 70, team_over=True) == "consider"
    assert derive_verdict(5, 2, 40, 70, team_over=True) == "skip"


def test_the_team_check() -> None:
    assert team_check(None, 8).status == "not_stated"
    assert team_check(None, 8).text == "Team: not stated."
    assert team_check(12, None).status == "not_stated"
    assert team_check(16, 8) == team_check(9, 8)
    assert team_check(16, 8).status == "ok"
    assert team_check(16, 8).text == "Team: within twice what you've led."
    over = team_check(40, 8)
    assert over.status == "over"
    assert "40" in over.text
    assert "8" in over.text


# -- the service


def _reply(*items: dict[str, object], team: int | None = 12, led: int | None = 8) -> str:
    return json.dumps({"must_haves": list(items), "ad_team_size": team, "largest_team_led": led})


ITEMS: tuple[dict[str, object], ...] = (
    {
        "requirement": "Kafka streaming",
        "match": "shown",
        "quote": "Built a streaming ingestion platform on Kafka and BigQuery handling 2 billion "
        "events a day.",
        "source": "cv_technical",
        "why": "Built and ran one at scale.",
    },
    {
        "requirement": "BigQuery cost control",
        "match": "shown",
        "quote": "Cut warehouse spend by 40% by moving batch jobs to scheduled queries.",
        "source": "cv_technical",
        "why": "A measured saving.",
    },
    {
        "requirement": "Observability",
        "match": "shown",
        "quote": "Chose and rolled out Grafana and OpenTelemetry for 30 services.",
        "source": "records",
        "record_id": str(RECORD.id),
        "why": "Picked and rolled out the stack.",
    },
    {
        "requirement": "dbt",
        "match": "shown",
        "quote": "Modelled everything in dbt.",
        "source": "cv_technical",
        "why": "Mentioned in passing.",
    },
    {
        "requirement": "Stakeholder management",
        "match": "shown",
        "quote": "Led a team of 8 engineers across two squads.",
        "source": "cv_leadership",
        "why": "Leading across squads.",
    },
)


@pytest.fixture
def domain() -> Domain:
    found = Domain()
    found.profiles.profiles[ALICE.user_id] = CandidateProfile(
        cv_technical=CV_TECH, cv_leadership=CV_LEAD, skills="dbt, Airflow"
    )
    return found


def _service(
    domain: Domain, experience: MemoryExperience, store: MemoryEvidence
) -> EvidenceTestService:
    return EvidenceTestService(
        role_service=domain.roles,
        roles=domain.roles_store,
        profiles=domain.profiles,
        experience=experience,
        store=store,
        llm=domain.llm,
        clock=domain.clock,
    )


async def _role(domain: Domain, score: int | None = 80) -> uuid.UUID:
    role = await domain.roles.create_role(
        ALICE, RoleDraft(title="Lead Data Engineer", company="Fabrikam", description=AD)
    )
    key = (ALICE.user_id, role.id)
    domain.roles_store.roles[key] = dataclasses.replace(
        domain.roles_store.roles[key], score=score, score_breakdown={"skills": 40}
    )
    return role.id


async def test_a_run_verifies_quotes_derives_the_verdict_and_updates_the_summary(
    domain: Domain,
) -> None:
    experience, store = MemoryExperience(), MemoryEvidence()
    experience.records[(ALICE.user_id, RECORD.id)] = RECORD
    service = _service(domain, experience, store)
    role_id = await _role(domain)
    domain.llm.script(LlmFeature.EVIDENCE, _reply(*ITEMS))

    result = await service.run(ALICE, role_id)

    assert [item.match for item in result.items] == ["shown", "shown", "shown", "partial", "shown"]
    assert (result.shown, result.total, result.from_records) == (4, 5, 1)
    assert result.verdict == "apply"
    assert result.verdict_text == (
        "Apply · you show 4 of 5 must-haves (1 from your records, not on your CV)"
    )
    assert result.team_check.status == "ok"
    assert result.items[2].record_title == "Observability across services"
    assert await service.latest(ALICE, role_id) == result
    request = domain.llm.requests[-1]
    assert request.feature is LlmFeature.EVIDENCE
    names = [block.name for block in request.data]
    assert names == ["candidate_evidence", "listing", "job_ad"]
    assert str(RECORD.id) in request.data[0].text
    assert "Riley" not in request.system + request.instructions

    role = domain.roles_store.roles[(ALICE.user_id, role_id)]
    summary = role_evidence(role.score, role.score_breakdown)
    assert summary is not None
    assert (summary.shown, summary.must_haves_total, summary.from_records) == (4, 5, 1)
    assert summary.verdict == "apply"
    assert role.score_breakdown is not None
    assert role.score_breakdown["skills"] == 40


async def test_the_verdict_follows_the_current_score_on_read(domain: Domain) -> None:
    store = MemoryEvidence()
    service = _service(domain, MemoryExperience(), store)
    role_id = await _role(domain, score=80)
    domain.llm.script(LlmFeature.EVIDENCE, _reply(*ITEMS[:2], ITEMS[4], team=None))
    assert (await service.run(ALICE, role_id)).verdict == "apply"
    key = (ALICE.user_id, role_id)
    domain.roles_store.roles[key] = dataclasses.replace(domain.roles_store.roles[key], score=30)
    latest = await service.latest(ALICE, role_id)
    assert (latest.verdict, latest.team_check.text) == ("consider", "Team: not stated.")


async def test_other_users_and_unchecked_roles_get_not_found(domain: Domain) -> None:
    service = _service(domain, MemoryExperience(), MemoryEvidence())
    role_id = await _role(domain)
    with pytest.raises(NotFound):
        await service.latest(ALICE, role_id)
    with pytest.raises(NotFound):
        await service.run(BOB, role_id)
    assert domain.llm.requests == []


async def test_a_role_without_ad_text_is_refused_before_any_call(domain: Domain) -> None:
    service = _service(domain, MemoryExperience(), MemoryEvidence())
    role = await domain.roles.create_role(ALICE, RoleDraft(title="Data Engineer"))
    with pytest.raises(ValidationFailed):
        await service.run(ALICE, role.id)
    assert domain.llm.requests == []


async def test_rescoring_keeps_the_last_evidence_summary(domain: Domain) -> None:
    service = _service(domain, MemoryExperience(), MemoryEvidence())
    role_id = await _role(domain)
    domain.llm.script(LlmFeature.EVIDENCE, _reply(*ITEMS[:2], ITEMS[4]))
    await service.run(ALICE, role_id)
    key = (ALICE.user_id, role_id)
    listed = {"location": "Melbourne", "work_mode": "hybrid", "pay": "$900 a day"}
    domain.roles_store.roles[key] = dataclasses.replace(domain.roles_store.roles[key], **listed)
    assessment: dict[str, object] = {
        "track": "contract",
        "mission": None,
        "breakdown": {"skills": 45, "track_pay": 20, "location": 15, "freshness": 9},
        "relevant": True,
        "reason": "Hands-on data role.",
        "risks": [],
        "work_mode": "hybrid",
    }
    domain.llm.script(LlmFeature.SCORE, json.dumps(assessment))
    rescored = await domain.scoring.score_role(ALICE, role_id)
    summary = role_evidence(rescored.score, rescored.score_breakdown)
    assert summary is not None
    assert (summary.shown, summary.must_haves_total) == (3, 3)
