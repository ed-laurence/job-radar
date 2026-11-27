"""Kit downloads (spec `getKitCoverLetterPdf`, `getKitCvPdf`), ported from job-seeker's
`cover_letter_pdf` / `cv_pdf` routes and `letter_pdf.py` (layout rules only; the renderer is an
adapter behind `LetterRenderer`).

- Cover letter: the kit's `cover_note` laid out like the user's own letters. The name and
  contact line come from the caller's profile settings; the date is today in their display
  time zone ("27 November 2026"); "To" is `letter_to`, else "Hiring Team, <company>"; the
  "Re:" line is `letter_re`, else the role title; `**text**` marks bold lead-ins.
- CV: the caller's stored PDF for the role's track (leadership for exec-track roles, else
  technical, falling back to whichever slot holds one). job-seeker's in-place tweaks aren't
  ported.

Both act only on the caller's own role (RLS) and never log content.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.errors import NotFound
from app.domain.identity import Principal
from app.domain.jobs.documents import StoredFile
from app.domain.jobs.ports import ProfileStore
from app.domain.jobs.records import RoleRecord
from app.domain.ports import Clock
from app.domain.user_settings import PrefsSource, prefs_for

LEADERSHIP_TRACKS = frozenset({"exec"})
CV_ORDER = {True: ("leadership", "technical"), False: ("technical", "leadership")}
CONTACT_SEPARATOR = "  ·  "
DEFAULT_NAME = "Candidate"
MAX_FILENAME = 120
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_UNSAFE = re.compile(r"[^\w\-]+")
_COMPANY_CUT = re.compile(r"\s+[-|" + chr(0x2013) + chr(0x2014) + r"]\s+|\s*\(|\s*,")


@dataclass(frozen=True, kw_only=True)
class Letter:
    """Everything the renderer lays out. `paragraphs` are already markup: escaped text with
    `<b>` for bold lead-ins and `<br/>` for line breaks."""

    name: str
    contact: str
    date: str
    to: str
    re_line: str
    paragraphs: tuple[str, ...]
    title: str


class LetterRenderer(Protocol):
    async def render(self, letter: Letter) -> bytes:
        """A4 PDF bytes, one page where it fits (shrinking the body type slightly first)."""
        ...


class RoleLookup(Protocol):
    async def get_role(self, principal: Principal, role_id: uuid.UUID) -> RoleRecord:
        """Raises `NotFound` for a role the caller can't see."""
        ...


class CvFiles(Protocol):
    async def download(self, principal: Principal, slot: str) -> StoredFile:
        """Raises `NotFound` when the slot holds no document."""
        ...


@dataclass(frozen=True, kw_only=True)
class KitFile:
    filename: str
    data: bytes


# ------------------------------------------------------------------ layout helpers (pure)


def letter_date(now: datetime, timezone: str) -> str:
    """'27 November 2026': today in the user's display time zone (UTC if it's unknown)."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    return f"{local.day} {local.strftime('%B %Y')}"


def short_company(company: str | None) -> str:
    """'Netwealth - See Wealth Differently (ASX:NWL)' -> 'Netwealth' (for file names)."""
    return _COMPANY_CUT.split(company or "")[0].strip()


def safe_filename(*parts: str) -> str:
    """Parts joined by '_', anything but word characters and '-' made '_', at most 120
    characters, with '.pdf'."""
    joined = "_".join(part for part in parts if part)
    cleaned = re.sub(r"_+", "_", _UNSAFE.sub("_", joined)).strip("_")[:MAX_FILENAME]
    return (cleaned or "document") + ".pdf"


def letter_to(role: RoleRecord) -> str:
    kit = role.kit
    if kit is not None and (kit.letter_to or "").strip():
        return (kit.letter_to or "").strip()
    return ", ".join(part for part in ("Hiring Team", (role.company or "").strip()) if part)


def re_line(role: RoleRecord) -> str:
    kit = role.kit
    if kit is not None and (kit.letter_re or "").strip():
        return (kit.letter_re or "").strip()
    return role.title


def paragraph_markup(text: str) -> str:
    """One paragraph as renderer markup: escaped, `**bold**` as `<b>`, line breaks kept."""
    out = escape(text.strip(), quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    return out.replace("\n", "<br/>")


def letter_paragraphs(body: str) -> tuple[str, ...]:
    """Blank-line separated paragraphs, with "Kind regards," and the signature split apart."""
    found: list[str] = []
    for block in re.split(r"\n\s*\n", body.strip()):
        if not block.strip():
            continue
        for part in re.split(r"(?<=Kind regards,)\s*\n", block):
            if part.strip():
                found.append(paragraph_markup(part))
    return tuple(found)


def contact_line(*parts: str | None) -> str:
    return CONTACT_SEPARATOR.join(part.strip() for part in parts if part and part.strip())


def cv_slots(role: RoleRecord) -> tuple[str, str]:
    """The CV slots to try for this role, preferred first."""
    return CV_ORDER[(role.track or "") in LEADERSHIP_TRACKS]


# ------------------------------------------------------------------ the service


@dataclass(kw_only=True)
class KitFileService:
    roles: RoleLookup
    profiles: ProfileStore
    cvs: CvFiles
    renderer: LetterRenderer
    clock: Clock
    prefs: PrefsSource | None = None

    async def _name(self, principal: Principal) -> tuple[str, str]:
        profile = await self.profiles.load_profile(principal.user_id)
        name = (profile.name or principal.display_name or DEFAULT_NAME).strip()
        email = profile.email or principal.email
        return name, contact_line(profile.location, email)

    async def cover_letter(self, principal: Principal, role_id: uuid.UUID) -> KitFile:
        """Raises `NotFound` for another user's role or one without a cover letter yet."""
        role = await self.roles.get_role(principal, role_id)
        body = (role.kit.cover_note or "") if role.kit is not None else ""
        if not body.strip():
            raise NotFound("This role has no cover letter yet. Write the kit first.")
        name, contact = await self._name(principal)
        prefs = await prefs_for(self.prefs, principal.user_id)
        letter = Letter(
            name=name,
            contact=contact,
            date=letter_date(self.clock.now(), prefs.timezone),
            to=letter_to(role),
            re_line=re_line(role),
            paragraphs=letter_paragraphs(body),
            title=f"{name} - Cover letter",
        )
        data = await self.renderer.render(letter)
        # The company only: a letter named after the employer is normal.
        filename = safe_filename(
            name.replace(" ", "_"), "Cover_Letter", short_company(role.company)
        )
        return KitFile(filename=filename, data=data)

    async def cv(self, principal: Principal, role_id: uuid.UUID) -> KitFile:
        """Raises `NotFound` for another user's role or when no CV is uploaded."""
        role = await self.roles.get_role(principal, role_id)
        name, _ = await self._name(principal)
        for slot in cv_slots(role):
            try:
                stored = await self.cvs.download(principal, slot)
            except NotFound:
                continue
            filename = safe_filename(name.replace(" ", "_"), "CV")
            return KitFile(filename=filename, data=stored.data)
        raise NotFound("No CV is uploaded yet. Add one in Settings.")
