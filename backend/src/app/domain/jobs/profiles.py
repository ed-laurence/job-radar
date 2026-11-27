"""The candidate profile with the user's settings applied: their `profile.skills` (extra
skills, treated as fact alongside the CVs) and `profile.brief`, `kits.spelling`, and the
de-identified text of their uploaded CVs and LinkedIn profile PDF.

Empty settings and no uploads leave the profile, and so every prompt, as it was (replay fixtures
still match).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Protocol

from app.domain.jobs.ports import ProfileStore
from app.domain.jobs.records import CandidateProfile
from app.domain.user_settings import PrefsSource


class DocumentTexts(Protocol):
    """The caller's current document texts by slot, already guarded and de-identified."""

    async def texts(self, user_id: uuid.UUID) -> dict[str, str]: ...


class ProfileWithSettings:
    """`ProfileStore` over another one, overlaying the user's settings."""

    def __init__(
        self, profiles: ProfileStore, prefs: PrefsSource, documents: DocumentTexts | None = None
    ) -> None:
        self._profiles = profiles
        self._prefs = prefs
        self._documents = documents

    async def load_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        profile = await self._profiles.load_profile(user_id)
        prefs = await self._prefs.for_user(user_id)
        texts = await self._documents.texts(user_id) if self._documents is not None else {}
        return replace(
            profile,
            skills=prefs.skills.strip() or None,
            brief=prefs.brief.strip() or None,
            spelling=prefs.kits.spelling,
            cv_technical=texts.get("technical") or None,
            cv_leadership=texts.get("leadership") or None,
            linkedin_profile=texts.get("linkedin_profile") or None,
        )
