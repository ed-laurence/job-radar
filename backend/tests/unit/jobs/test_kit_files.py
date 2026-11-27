"""Kit downloads: the cover-letter layout helpers (date, file name, bold markup, To/Re
fallbacks), the CV choice by track, and the ReportLab renderer. Fictional names only."""

from __future__ import annotations

import base64
import io
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader

from app.adapters.fakes.jobs import MemoryProfiles, MemoryRoles
from app.adapters.fakes.profile import FakePdfText
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, InMemoryStorage, SequentialIds
from app.adapters.pdf_letter import render_letter
from app.domain.errors import NotFound
from app.domain.jobs.documents import ProfileDocumentService
from app.domain.jobs.kit_files import (
    KitFileService,
    Letter,
    contact_line,
    cv_slots,
    letter_date,
    letter_paragraphs,
    letter_to,
    paragraph_markup,
    re_line,
    safe_filename,
    short_company,
)
from app.domain.jobs.records import CandidateProfile, KitRecord, RoleRecord
from app.domain.jobs.roles import RoleDraft, RoleService
from tests.unit.jobs.support import ALICE, BOB

AT = datetime(2026, 11, 26, 14, 30, tzinfo=UTC)  # 27 Nov, 01:30 in Melbourne


BASE = RoleRecord(
    id=uuid.UUID(int=7),
    source="manual",
    title="Lead Data Engineer",
    stage="new",
    status="new",
    rank="a0",
    version=1,
    found_at=AT,
    created_at=AT,
    updated_at=AT,
    company="Fabrikam Freight",
)


def _kit(
    cover_note: str | None = None, letter_to: str | None = None, letter_re: str | None = None
) -> KitRecord:
    return KitRecord(
        role_id=BASE.id,
        version=1,
        generated_at=AT,
        cover_note=cover_note,
        letter_to=letter_to,
        letter_re=letter_re,
    )


# -- layout helpers


def test_letter_date_is_today_in_the_display_time_zone() -> None:
    assert letter_date(AT, "Australia/Melbourne") == "27 November 2026"
    assert letter_date(AT, "UTC") == "26 November 2026"
    assert letter_date(AT, "Not/AZone") == "26 November 2026"  # unknown zone: UTC


def test_file_names_are_sanitised() -> None:
    assert short_company("Netwealth - See Wealth Differently (ASX:NWL)") == "Netwealth"
    assert short_company("Contoso, Melbourne") == "Contoso"
    assert short_company(None) == ""
    name = safe_filename("Alex_Example", "Cover_Letter", short_company("Fabrikam & Co (AU)"))
    assert name == "Alex_Example_Cover_Letter_Fabrikam_Co.pdf"
    assert safe_filename("../../etc/passwd", "CV") == "etc_passwd_CV.pdf"
    assert safe_filename("", "") == "document.pdf"
    assert len(safe_filename("x" * 300)) == 124


def test_bold_lead_ins_are_markup_and_everything_else_is_escaped() -> None:
    markup = paragraph_markup("**Data platforms.** I built <one> & ran it\nfor years.")
    assert markup == "<b>Data platforms.</b> I built &lt;one&gt; &amp; ran it<br/>for years."
    body = "Dear team,\n\n**Why me.** Because.\n\nKind regards,\nAlex Example"
    assert letter_paragraphs(body) == (
        "Dear team,",
        "<b>Why me.</b> Because.",
        "Kind regards,",
        "Alex Example",
    )


def test_to_and_re_fall_back_to_the_role() -> None:
    bare = replace(BASE, kit=_kit(cover_note="Hi"))
    assert letter_to(bare) == "Hiring Team, Fabrikam Freight"
    assert letter_to(replace(BASE, company=None)) == "Hiring Team"
    assert re_line(bare) == "Lead Data Engineer"
    named = replace(
        BASE, kit=_kit(letter_to="Sam Okafor, Head of Data", letter_re="Data Lead (ref 42)")
    )
    assert letter_to(named) == "Sam Okafor, Head of Data"
    assert re_line(named) == "Data Lead (ref 42)"
    assert contact_line("Melbourne VIC", None, " alex@example.test ") == (
        "Melbourne VIC  ·  alex@example.test"
    )


def test_cv_slot_follows_the_track() -> None:
    assert cv_slots(replace(BASE, track="exec")) == ("leadership", "technical")
    assert cv_slots(replace(BASE, track="contract")) == ("technical", "leadership")
    assert cv_slots(BASE) == ("technical", "leadership")


# -- the renderer


def _letter(paragraphs: tuple[str, ...]) -> Letter:
    return Letter(
        name="Alex Example",
        contact="Melbourne VIC  ·  alex@example.test",
        date="27 November 2026",
        to="Hiring Team, Fabrikam Freight",
        re_line="Lead Data Engineer",
        paragraphs=paragraphs,
        title="Alex Example - Cover letter",
    )


def test_the_letter_renders_as_one_a4_page() -> None:
    data = render_letter(_letter(("Dear team,", "<b>Why me.</b> Because.", "Kind regards,")))
    assert data.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (595, 842)
    text = page.extract_text()
    assert "Re: Lead Data Engineer" in text
    assert "27 November 2026" in text


def test_a_long_letter_shrinks_to_fit_one_page() -> None:
    long = tuple("Sentence about delivery and teams. " * 18 for _ in range(7))
    assert len(PdfReader(io.BytesIO(render_letter(_letter(long)))).pages) == 1


# -- the service


class FakeRenderer:
    def __init__(self) -> None:
        self.letters: list[Letter] = []

    async def render(self, letter: Letter) -> bytes:
        self.letters.append(letter)
        return b"%PDF-fake letter"


@dataclass
class Parts:
    service: KitFileService
    roles: MemoryRoles
    profiles: MemoryProfiles
    documents: ProfileDocumentService
    renderer: FakeRenderer
    role_service: RoleService


@pytest.fixture
def parts() -> Parts:
    clock, ids = FakeClock(AT), SequentialIds()
    roles_store, profiles = MemoryRoles(), MemoryProfiles()
    documents = ProfileDocumentService(
        InMemoryStorage(), FakePdfText(), FakeContentGuard(), FakePII(), clock, ids
    )
    renderer = FakeRenderer()
    role_service = RoleService(roles_store, FakeContentGuard(), clock, ids)
    service = KitFileService(
        roles=role_service, profiles=profiles, cvs=documents, renderer=renderer, clock=clock
    )
    return Parts(service, roles_store, profiles, documents, renderer, role_service)


async def _new_role(parts: Parts, cover_note: str | None) -> uuid.UUID:
    draft = RoleDraft(title="Lead Data Engineer", company="Fabrikam Freight (AU)")
    role = await parts.role_service.create_role(ALICE, draft)
    if cover_note is not None:
        values = {"cover_note": cover_note, "generated_at": AT}
        await parts.roles.save_kit(ALICE.user_id, role.id, values)
    return role.id


async def test_cover_letter_uses_the_profile_and_kit(parts: Parts) -> None:
    parts.profiles.profiles[ALICE.user_id] = CandidateProfile(
        name="Alex Example", location="Melbourne VIC"
    )
    role_id = await _new_role(parts, "Dear team,\n\n**Why me.** Because.")
    found = await parts.service.cover_letter(ALICE, role_id)
    assert found.filename == "Alex_Example_Cover_Letter_Fabrikam_Freight.pdf"
    assert found.data.startswith(b"%PDF-")
    (letter,) = parts.renderer.letters
    assert letter.contact == "Melbourne VIC  ·  alice@example.test"  # no profile email: login
    assert letter.to == "Hiring Team, Fabrikam Freight (AU)"
    assert letter.re_line == "Lead Data Engineer"
    assert letter.date == "27 November 2026"  # the default display zone, Melbourne
    assert letter.paragraphs == ("Dear team,", "<b>Why me.</b> Because.")


async def test_no_cover_letter_or_another_users_role_is_not_found(parts: Parts) -> None:
    without = await _new_role(parts, None)
    with pytest.raises(NotFound, match="no cover letter"):
        await parts.service.cover_letter(ALICE, without)
    written = await _new_role(parts, "Hello")
    with pytest.raises(NotFound):
        await parts.service.cover_letter(BOB, written)
    with pytest.raises(NotFound):
        await parts.service.cv(BOB, written)


def _upload(text: str) -> str:
    return base64.b64encode(f"%PDF-fake pages=1\n{text}".encode()).decode()


async def test_cv_prefers_the_track_slot_and_falls_back(parts: Parts) -> None:
    parts.profiles.profiles[ALICE.user_id] = CandidateProfile(name="Alex Example")
    role_id = await _new_role(parts, None)
    with pytest.raises(NotFound, match="No CV"):
        await parts.service.cv(ALICE, role_id)
    lead, tech = _upload("Leadership CV"), _upload("Technical CV")
    await parts.documents.upload(ALICE, "leadership", "lead.pdf", lead)
    found = await parts.service.cv(ALICE, role_id)  # technical wanted; only leadership exists
    assert (found.filename, found.data) == ("Alex_Example_CV.pdf", base64.b64decode(lead))
    await parts.documents.upload(ALICE, "technical", "tech.pdf", tech)
    assert (await parts.service.cv(ALICE, role_id)).data == base64.b64decode(tech)
    key = (ALICE.user_id, role_id)
    parts.roles.roles[key] = replace(parts.roles.roles[key], track="exec")
    assert (await parts.service.cv(ALICE, role_id)).data == base64.b64decode(lead)
