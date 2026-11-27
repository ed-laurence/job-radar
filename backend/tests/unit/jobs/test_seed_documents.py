"""The demo CVs (fictional Alex Example) reach the owner's profile documents through the upload
path: guarded, de-identified, stored with a manifest, and never uploaded twice."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, InMemoryStorage, SequentialIds
from app.adapters.pdf import PypdfText
from app.domain.identity import AppRole, Principal
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.jobs.experience import PROJECTS, contexts_from
from app.jobs.seed_documents import seed_documents
from app.settings import REPO_ROOT

FOLDER = REPO_ROOT / "seed" / "documents"
OWNER = Principal(user_id=uuid.UUID(int=11), email="owner@example.test", role=AppRole.MAINTAINER)
ROLES = (
    "Principal Engineer, Saltbush Digital Services",
    "Engineering Manager, Tasman Analytics Services",
    "Head of Data, Sapphire Learning",
    "Analytics Lead, Lumen Partners Institute",
    "Data Engineer, Orbital Payments Co",
    "Software Engineer, Ridgeline Control Systems",
)


def _service() -> tuple[ProfileDocumentService, FakeContentGuard, InMemoryStorage]:
    guard, storage = FakeContentGuard(), InMemoryStorage()
    service = ProfileDocumentService(
        storage, PypdfText(), guard, FakePII(), FakeClock(), SequentialIds()
    )
    return service, guard, storage


async def test_demo_cvs_are_uploaded_once_through_the_guarded_path() -> None:
    service, guard, storage = _service()
    assert await seed_documents(service, OWNER, FOLDER) == ["technical", "leadership"]
    listed = await service.list_documents(OWNER)
    assert [(d.slot, d.filename, d.archived_versions) for d in listed] == [
        ("technical", "technical-cv.pdf", 0),
        ("leadership", "leadership-cv.pdf", 0),
    ]
    assert all(d.readability.ok for d in listed)
    assert len(guard.inspected_inputs) == 2
    texts = await service.texts(OWNER.user_id)
    assert "alex@example.test" not in texts["technical"]  # de-identified like any upload
    assert contexts_from(texts) == (*ROLES, *PROJECTS)

    objects = dict(storage.objects)
    assert await seed_documents(service, OWNER, FOLDER) == []
    assert storage.objects == objects


async def test_a_filled_slot_is_left_alone_and_missing_files_are_skipped(tmp_path: Path) -> None:
    service, _, _ = _service()
    (tmp_path / "technical-cv.pdf").write_bytes((FOLDER / "technical-cv.pdf").read_bytes())
    assert await seed_documents(service, OWNER, tmp_path) == ["technical"]
    assert await seed_documents(service, OWNER, FOLDER) == ["leadership"]
    other = Principal(user_id=uuid.UUID(int=12), email="tester@example.test", role=AppRole.USER)
    assert await service.list_documents(other) == []
