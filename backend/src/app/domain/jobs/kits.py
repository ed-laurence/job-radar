"""Application kits, streamed part by part (ported from job-seeker's `kits.py`).

Each requested part is one streamed LLM call (feature `kit`). Deltas pass the segment filter
before release; the stored kit holds only the filtered text. The code-level quality bar
(`kit_checks`) runs on the finished parts and decides the `done` status.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import Conflict, DomainError
from app.domain.identity import Principal
from app.domain.jobs.kit_checks import kit_problems, parse_answers
from app.domain.jobs.ports import (
    KitStore,
    ProfileStore,
    Redaction,
)
from app.domain.jobs.prompts import FACT_RULES, VOICE_RULES, role_blocks
from app.domain.jobs.records import CandidateProfile, KitAnswerRecord, RoleRecord
from app.domain.jobs.roles import RoleService
from app.domain.jobs.streams import Done, Failed, SegmentRelay, StreamEvent
from app.domain.ports import LLM, Clock, DataBlock, LlmAttribution, LlmFeature, LlmRequest

PARTS = ("cv", "cover_note", "outreach", "answers")

KIT_SYSTEM = (
    "You write one part of a job application kit for the candidate described in the candidate "
    "profile. They read it, fix anything that doesn't sound like them and send it themselves, so "
    "it must be accurate and ready to use. Reply with the requested text only."
)
PART_INSTRUCTIONS = {
    "cv": (
        "Write a short tailoring note for the candidate's CV: a one-line tagline aligned with "
        "this role, then two or three bullet points saying which existing experience to "
        "foreground and which listed skills to move first. Only re-emphasise what they have."
    ),
    "cover_note": (
        "Write the cover letter, from the salutation to the sign-off. 300-550 words. Salutation "
        '"Dear {first name}," when the ad names the hiring contact, else "Dear Hiring Team,". '
        "Open with the role and organisation, anchor three or four body paragraphs on "
        "the candidate's experience records ordered by the ad's top requirements, address any "
        "listed selection criteria in turn, and say honestly if something would be new to them. "
        'End with "Kind regards," and the candidate\'s name on the next line.'
    ),
    "outreach": (
        "Write a LinkedIn note of at most 300 characters to the likely hiring manager: one "
        "specific, true thing about the role, then that the candidate is applying."
    ),
    "answers": (
        "Answer likely screening questions (or the questions given) in 2-4 sentences each. "
        "Format every item as a line starting `Q:` with the question, then a line starting `A:` "
        "with the answer. Answer every given question first."
    ),
}


@dataclass(frozen=True)
class KitRequestInput:
    parts: tuple[str, ...] = PARTS
    questions: tuple[str, ...] = ()


class KitService:
    def __init__(
        self,
        role_service: RoleService,
        kits: KitStore,
        profiles: ProfileStore,
        llm: LLM,
        redaction: Redaction,
        clock: Clock,
    ) -> None:
        self._role_service = role_service
        self._kits = kits
        self._profiles = profiles
        self._llm = llm
        self._redaction = redaction
        self._clock = clock
        self._busy: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def open_kit(
        self, principal: Principal, role_id: uuid.UUID, request: KitRequestInput
    ) -> AsyncIterator[StreamEvent]:
        """Validate and lock before the stream starts, so errors are plain problem responses."""
        role = await self._role_service.get_role(principal, role_id)
        key = (principal.user_id, role_id)
        if key in self._busy:
            raise Conflict("A kit is already being generated for this role.")
        self._busy.add(key)
        parts = tuple(part for part in PARTS if part in (request.parts or PARTS))
        return self._run(principal, role, parts, request.questions)

    async def _run(
        self,
        principal: Principal,
        role: RoleRecord,
        parts: tuple[str, ...],
        questions: tuple[str, ...],
    ) -> AsyncIterator[StreamEvent]:
        relay = SegmentRelay(self._redaction)
        try:
            profile = await self._profiles.load_profile(principal.user_id)
            for part in parts:
                request = self._request(principal, role, profile, part, questions)
                async for event in relay.relay(self._llm.stream(request), part):
                    yield event
            status = await self._save(principal, role, profile, relay.texts)
            yield Done(seq=relay.next_seq(), result_id=role.id, status=status)
        except DomainError as error:
            yield Failed(seq=relay.next_seq(), error=error)
        finally:
            self._busy.discard((principal.user_id, role.id))

    def _request(
        self,
        principal: Principal,
        role: RoleRecord,
        profile: CandidateProfile,
        part: str,
        questions: Sequence[str],
    ) -> LlmRequest:
        blocks = list(role_blocks(role, profile))
        if part == "answers" and questions:
            blocks.append(DataBlock("questions", "\n".join(f"- {q}" for q in questions)))
        instructions = f"{PART_INSTRUCTIONS[part]}\n\n{FACT_RULES}\n{VOICE_RULES}"
        return LlmRequest(
            feature=LlmFeature.KIT,
            system=KIT_SYSTEM,
            instructions=instructions,
            attribution=LlmAttribution(user_id=principal.user_id, role_id=role.id),
            data=tuple(blocks),
        )

    async def _save(
        self,
        principal: Principal,
        role: RoleRecord,
        profile: CandidateProfile,
        texts: dict[str, str],
    ) -> str:
        answers = parse_answers(texts["answers"]) if "answers" in texts else None
        sources = "\n".join(
            [role.description or "", *(d for item in profile.experience for d in item.details)]
        )
        problems = kit_problems(texts, answers or [], profile, sources)
        now = self._clock.now()
        await self._kits.save_kit(
            principal.user_id, role.id, _kit_values(role, texts, answers, now)
        )
        summary = "ok" if not problems else f"{len(problems)} checks failed"
        await self._role_service.record_event(principal, role.id, "kit_regenerated", summary)
        return "ok" if not problems else "needs_review"


def _kit_values(
    role: RoleRecord,
    texts: dict[str, str],
    answers: list[KitAnswerRecord] | None,
    now: datetime,
) -> dict[str, object]:
    """Merge the regenerated parts over the existing kit, so a partial run keeps the rest."""
    old = role.kit
    old_answers = [{"q": a.q, "a": a.a} for a in old.answers] if old else []
    return {
        "cv": texts.get("cv", old.cv if old else None),
        "cover_note": texts.get("cover_note", old.cover_note if old else None),
        "outreach": texts.get("outreach", old.outreach if old else None),
        "answers": [{"q": a.q, "a": a.a} for a in answers] if answers is not None else old_answers,
        "letter_re": role.title,
        "letter_to": f"Hiring Team, {role.company}" if role.company else "Hiring Team",
        "contact": old.contact if old else None,
        "generated_at": now,
    }
