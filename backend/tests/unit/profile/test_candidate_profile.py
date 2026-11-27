"""The candidate profile used by scoring and kits: CV text (guarded and de-identified), extra
skills and experience records. With nothing set, the profile block is unchanged."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass

import pytest

from app.adapters.fakes.jobs import MemoryProfiles
from app.adapters.fakes.profile import FakePdfText, MemoryExperience
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, InMemoryStorage, SequentialIds
from app.adapters.fakes.user_settings import MemoryUserSettings
from app.domain.errors import GuardBlocked
from app.domain.identity import AppRole, Principal
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.jobs.experience import EntryText, ExperienceDraft, ExperienceService
from app.domain.jobs.profiles import ProfileWithSettings
from app.domain.jobs.prompts import profile_block
from app.domain.user_settings import SettingsService

ME = Principal(user_id=uuid.uuid4(), email="me@example.test", role=AppRole.USER)
OTHER = Principal(user_id=uuid.uuid4(), email="other@example.test", role=AppRole.USER)


def _pdf(text: str) -> str:
    return base64.b64encode(f"%PDF-fake pages=1\n{text}".encode()).decode()


@dataclass
class Parts:
    documents: ProfileDocumentService
    experience: ExperienceService
    profiles: ProfileWithSettings
    guard: FakeContentGuard
    storage: InMemoryStorage


@pytest.fixture
def parts() -> Parts:
    clock, ids = FakeClock(), SequentialIds()
    storage, guard = InMemoryStorage(), FakeContentGuard()
    documents = ProfileDocumentService(storage, FakePdfText(), guard, FakePII(), clock, ids)
    experience_store = MemoryExperience()
    experience = ExperienceService(experience_store, clock, ids)
    prefs = SettingsService(MemoryUserSettings(clock))
    profiles = ProfileWithSettings(MemoryProfiles(experience_store), prefs, documents)
    return Parts(documents, experience, profiles, guard, storage)


async def test_empty_profile_block_is_unchanged(parts: Parts) -> None:
    block = profile_block(await parts.profiles.load_profile(ME.user_id))
    assert block.text == profile_block(await MemoryProfiles().load_profile(ME.user_id)).text
    assert "cv_" not in block.text


async def test_cv_text_is_guarded_deidentified_and_per_user(parts: Parts) -> None:
    text = "Summary\nReach me at someone@example.test\nExperience\nSkills"
    await parts.documents.upload(ME, "technical", "cv.pdf", _pdf(text))
    assert text in parts.guard.inspected_inputs
    block = profile_block(await parts.profiles.load_profile(ME.user_id)).text
    assert "cv_technical" in block
    assert "someone@example.test" not in block
    assert "Experience" in block
    other = profile_block(await parts.profiles.load_profile(OTHER.user_id)).text
    assert "cv_technical" not in other
    assert all(key.startswith(f"users/{ME.user_id}/profile/") for key in parts.storage.objects)


async def test_blocked_text_is_never_stored(parts: Parts) -> None:
    with pytest.raises(GuardBlocked):
        await parts.documents.upload(ME, "leadership", "cv.pdf", _pdf("[[guard:block]]"))
    assert parts.storage.objects == {}


async def test_experience_records_reach_the_profile(parts: Parts) -> None:
    draft = ExperienceDraft(
        requirement="Ran a data platform",
        entries=(
            EntryText(context="Platform lead, Example Co", detail="Ran the data platform."),
            EntryText(context="Other", detail="Cut costs by a third."),
        ),
    )
    await parts.experience.create(ME, draft)
    profile = await parts.profiles.load_profile(ME.user_id)
    (item,) = profile.experience
    assert item.requirement == "Ran a data platform"
    assert item.details == (
        "Platform lead, Example Co: Ran the data platform.",
        "Other: Cut costs by a third.",
    )
    assert (await parts.profiles.load_profile(OTHER.user_id)).experience == ()


async def test_replaced_documents_are_archived_and_capped(parts: Parts) -> None:
    for n in range(7):
        await parts.documents.upload(ME, "technical", f"v{n}.pdf", _pdf(f"Summary {n}"))
    info = await parts.documents.upload(ME, "technical", "v7.pdf", _pdf("Summary 7"))
    assert info.archived_versions == 5
    # current + 5 archived, each a PDF and its text, plus the manifest
    assert len(parts.storage.objects) == 6 * 2 + 1
