"""Experience records (job-seeker's model): a requirement plus one entry per role or project.
Cleaning and field errors, positional refs, `uses` from stored evidence tests, the CV-role
contexts and how records read in prompts. Fictional material only."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.fakes.evidence import MemoryEvidence
from app.adapters.fakes.profile import FakePdfText, MemoryExperience
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, InMemoryStorage, SequentialIds
from app.domain.errors import ValidationFailed
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.jobs.evidence_test import (
    EvidenceTestItem,
    EvidenceTestRecord,
    TeamCheck,
    evidence_block,
    record_uses,
)
from app.domain.jobs.experience import (
    MAX_ENTRIES,
    PROJECTS,
    EntryText,
    ExperienceDraft,
    ExperienceEntryRecord,
    ExperienceService,
    as_profile_item,
    clean_draft,
    contexts_from,
)
from app.domain.jobs.records import CandidateProfile
from app.domain.jobs.roles import RoleDraft
from tests.unit.jobs.support import ALICE, BOB, Domain

AT = datetime(2026, 11, 26, 7, 2, tzinfo=UTC)
ENTRY = EntryText(context="Platform lead, Example Co", detail="Ran the data platform.")


def _draft(*entries: EntryText, requirement: str = "Ran a data platform") -> ExperienceDraft:
    return ExperienceDraft(requirement=requirement, entries=entries or (ENTRY,))


def _pointers(error: pytest.ExceptionInfo[ValidationFailed]) -> list[str]:
    return [field.pointer for field in error.value.errors]


# -- cleaning


def test_whitespace_is_collapsed_and_blank_rows_are_dropped() -> None:
    draft = _draft(
        EntryText(context="  ", detail=""),
        EntryText(context=" Other ", detail="Cut\n costs   by a third."),
        requirement="  Ran   a platform ",
    )
    cleaned = clean_draft(draft)
    assert cleaned.requirement == "Ran a platform"
    assert cleaned.entries == (EntryText(context="Other", detail="Cut costs by a third."),)


def test_half_filled_rows_and_empty_records_are_field_errors() -> None:
    with pytest.raises(ValidationFailed) as half:
        clean_draft(_draft(ENTRY, EntryText(context="", detail="Did it.")))
    assert _pointers(half) == ["/entries/1/context"]
    with pytest.raises(ValidationFailed) as empty:
        clean_draft(_draft(EntryText(context="", detail=" "), requirement=" "))
    assert _pointers(empty) == ["/requirement", "/entries"]


def test_limits_are_field_errors() -> None:
    with pytest.raises(ValidationFailed) as many:
        clean_draft(_draft(*([ENTRY] * (MAX_ENTRIES + 1))))
    assert _pointers(many) == ["/entries"]
    long = EntryText(context="x" * 201, detail="y" * 1501)
    with pytest.raises(ValidationFailed) as too_long:
        clean_draft(_draft(long, requirement="z" * 201))
    assert _pointers(too_long) == ["/requirement", "/entries/0/context", "/entries/0/detail"]


# -- the service


@pytest.fixture
def domain() -> Domain:
    return Domain()


def _service(
    domain: Domain, experience: MemoryExperience, evidence: MemoryEvidence | None = None
) -> ExperienceService:
    return ExperienceService(
        experience, domain.clock, domain.ids, usage=evidence, roles=domain.roles
    )


async def test_refs_are_positional_and_entries_keep_their_order(domain: Domain) -> None:
    service = _service(domain, MemoryExperience())
    first = await service.create(ALICE, _draft())
    domain.clock.advance(timedelta(seconds=1))
    second = await service.create(
        ALICE, _draft(ENTRY, EntryText(context="Other", detail="Cut costs."))
    )
    assert (first.ref, second.ref) == ("E1", "E2")
    assert [(e.ref, e.context) for e in second.entries] == [
        ("E2.1", ENTRY.context),
        ("E2.2", "Other"),
    ]
    await service.delete(ALICE, first.id, 1)
    (only,) = await service.list_records(ALICE)
    assert (only.id, only.ref, only.entries[0].ref) == (second.id, "E1", "E1.1")
    assert await service.list_records(BOB) == []


async def test_a_role_id_must_be_the_callers_role(domain: Domain) -> None:
    service = _service(domain, MemoryExperience())
    role = await domain.roles.create_role(ALICE, RoleDraft(title="Lead Engineer"))
    linked = ExperienceDraft(requirement="Led a team", entries=(ENTRY,), role_id=role.id)
    assert (await service.create(ALICE, linked)).role_id == role.id
    with pytest.raises(ValidationFailed) as theirs:
        await service.create(BOB, linked)
    assert _pointers(theirs) == ["/role_id"]


def _test(role_id: uuid.UUID, *items: tuple[str, uuid.UUID | None]) -> EvidenceTestRecord:
    return EvidenceTestRecord(
        role_id=role_id,
        verdict="consider",
        verdict_text="",
        shown=0,
        total=len(items),
        from_records=0,
        items=tuple(
            EvidenceTestItem(
                requirement=f"Must-have {n}",
                match="shown" if match == "shown" else "partial",
                source="records" if record_id else "none",
                why="",
                record_id=record_id,
            )
            for n, (match, record_id) in enumerate(items)
        ),
        team_check=TeamCheck(status="not_stated", text="Team: not stated."),
        checked_at=AT,
        model="scripted",
    )


async def test_uses_count_roles_whose_stored_test_shows_the_record(domain: Domain) -> None:
    experience, evidence = MemoryExperience(), MemoryEvidence()
    service = _service(domain, experience, evidence)
    record = await service.create(ALICE, _draft())
    one, two, three = uuid.UUID(int=101), uuid.UUID(int=102), uuid.UUID(int=103)
    # Two must-haves on one role count once; a partial match doesn't count.
    evidence.tests[(ALICE.user_id, one)] = _test(one, ("shown", record.id), ("shown", record.id))
    evidence.tests[(ALICE.user_id, two)] = _test(two, ("shown", record.id))
    evidence.tests[(ALICE.user_id, three)] = _test(three, ("partial", record.id))
    evidence.tests[(BOB.user_id, one)] = _test(one, ("shown", record.id))
    (listed,) = await service.list_records(ALICE)
    assert listed.uses == 2


def test_record_uses_counts_each_test_once() -> None:
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    assert record_uses([[a, a, b], [a], []]) == {a: 2, b: 1}


# -- contexts


def test_contexts_put_the_technical_cv_first_and_skip_earlier_career() -> None:
    technical = (
        "EXPERIENCE\nData Engineer\nExample Payments | Victoria | 2012 - 2016\n"
        "Software Engineer\nExample Controls | Victoria | 2009 - 2012\nEDUCATION"
    )
    leadership = (
        "EXPERIENCE\nHead of Data\nExample Learning | Victoria | 2019 - 2021\n"
        "Data Engineer\nExample Payments | Victoria | 2012 - 2016\n"
        "Earlier career\nExample Controls | Victoria | 2009 - 2012\nSKILLS"
    )
    found = contexts_from({"leadership": leadership, "technical": technical})
    assert found == (
        "Data Engineer, Example Payments",
        "Software Engineer, Example Controls",
        "Head of Data, Example Learning",
        *PROJECTS,
    )
    assert contexts_from({}) == PROJECTS
    assert contexts_from({"linkedin_profile": technical}) == PROJECTS


async def test_overview_reads_contexts_from_the_callers_stored_cvs() -> None:
    clock, ids = FakeClock(), SequentialIds()
    documents = ProfileDocumentService(
        InMemoryStorage(), FakePdfText(), FakeContentGuard(), FakePII(), clock, ids
    )
    text = "EXPERIENCE\nData Engineer\nExample Payments | Jan 2012 - Mar 2016\nEDUCATION"
    data = base64.b64encode(f"%PDF-fake pages=1\n{text}".encode()).decode()
    await documents.upload(ALICE, "technical", "cv.pdf", data)
    service = ExperienceService(MemoryExperience(), clock, ids, cvs=documents)
    mine = await service.overview(ALICE)
    assert mine.contexts == ("Data Engineer, Example Payments", *PROJECTS)
    assert (await service.overview(BOB)).contexts == PROJECTS


# -- how records reach prompts


def test_records_read_as_requirement_and_one_detail_per_entry() -> None:
    cleaned = clean_draft(_draft(ENTRY, EntryText(context="Other", detail="Cut costs.")))
    record = ExperienceEntryRecord(
        id=uuid.UUID(int=5),
        requirement=cleaned.requirement,
        entries=cleaned.entries,
        version=1,
        created_at=AT,
        updated_at=AT,
    )
    item = as_profile_item(record)
    assert item.requirement == "Ran a data platform"
    assert item.details == (
        "Platform lead, Example Co: Ran the data platform.",
        "Other: Cut costs.",
    )
    block = evidence_block(CandidateProfile(), [record]).text
    assert str(record.id) in block
    assert '"context": "Other"' in block
    assert "Cut costs." in block
