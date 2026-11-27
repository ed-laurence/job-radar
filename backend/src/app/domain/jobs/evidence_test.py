"""The full evidence test (spec `getRoleEvidence`, `runRoleEvidence`), after job-seeker's.

One structured LLM call (feature `evidence`) extracts the ad's must-haves and answers each
against the candidate's own texts: the CVs (by slot), their extra skills and their experience
records (with ids). Code then does everything a recruiter would check:

- **Verbatim quotes.** A quote must appear in the text it claims to come from (whitespace and
  case aside). A quote found in a different CV or record is re-attributed; one found nowhere
  is dropped and a `shown` match is downgraded to `partial`, so a tick always points at a line
  the user can see.
- **Verdict.** apply when every must-have, or all but one, is shown, the score is at least the
  user's keep threshold and the team isn't over; consider when it passes on evidence or on
  score; otherwise skip. It is worked out again on read, from the current score and settings.
- **Team check.** The ad's team size against the largest team the candidate's texts show:
  ok within twice that, over beyond it, not_stated when either is unknown.

Guards and PII apply on the way in and out (the LLM adapter); the ad is untrusted data.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import NotFound, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.evidence import (
    SUMMARY_KEY,
    Verdict,
    derive_verdict,
    verdict_text,
)
from app.domain.jobs.experience import (
    MAX_RECORDS,
    ExperienceEntryRecord,
    ExperienceStore,
    entry_line,
)
from app.domain.jobs.ports import ProfileStore, RoleStore, RoleWrite
from app.domain.jobs.prompts import MAX_AD_CHARS
from app.domain.jobs.records import CandidateProfile, RoleRecord
from app.domain.jobs.roles import RoleService
from app.domain.ports import LLM, Clock, DataBlock, LlmAttribution, LlmFeature, LlmRequest
from app.domain.user_settings import DEFAULTS, PrefsSource

MAX_ITEMS = 12
MAX_MUST_HAVES = 8
MAX_QUOTE = 600
MAX_WHY = 200
MAX_REQUIREMENT = 200
TEAM_FACTOR = 2  # "within about twice what you've led"

type Match = Literal["shown", "partial", "missing"]
type Source = Literal["cv_technical", "cv_leadership", "records", "none"]
type TeamStatus = Literal["ok", "over", "not_stated"]
CV_SOURCES: tuple[Source, ...] = ("cv_technical", "cv_leadership")
NOT_BACKED = "No exact line on your CV or records backs this up."

EVIDENCE_SYSTEM = (
    "You run a strict evidence test: does the candidate's own material already prove what a "
    "job ad requires? Be consistent; the same ad and material must get the same answer."
)
EVIDENCE_INSTRUCTIONS = """Run the evidence test on the job ad against the candidate_evidence block.
- must_haves: the ad's top must-have requirements (at most five), most important first: what it
  says is essential or required, else what it stresses most. For each:
  - requirement: a few words, in the ad's terms.
  - match: "shown" only if a line in cv_technical, cv_leadership or one of the records shows the
    candidate has done it. A skills-list keyword, a reinterpretation or a generous reading is not
    shown. "partial" if they have done something close they could argue for in a conversation.
    "missing" if nothing in their material is like it.
  - quote: for shown or partial, the single best line, copied exactly, character for character,
    from the text it comes from (at most 600 characters). Empty string otherwise. Never
    paraphrase, merge or tidy a quote.
  - source: where the quote comes from: cv_technical, cv_leadership, records, or none. Prefer the
    CV that suits the role (leadership for leadership roles, technical for hands-on ones) and a CV
    line over a record when both prove it.
  - record_id: the record's id when source is records, else null.
  - why: one short line (under 200 characters) on what the quote proves, or what's missing.
- ad_team_size: how many people the role would lead in total, only if the ad states or clearly
  implies it; else null.
- largest_team_led: the largest team the candidate's CV or records say they have led, as a
  number; else null.
Use the spelling given in candidate_evidence."""


class MustHaveAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(max_length=MAX_REQUIREMENT)
    match: Match
    quote: str = Field(default="", max_length=MAX_QUOTE)
    source: Source = "none"
    record_id: str | None = Field(default=None, max_length=64)
    why: str = Field(default="", max_length=400)


class EvidenceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_haves: list[MustHaveAnswer] = Field(min_length=1, max_length=MAX_MUST_HAVES)
    ad_team_size: int | None = Field(default=None, ge=0, le=100_000)
    largest_team_led: int | None = Field(default=None, ge=0, le=100_000)


@dataclass(frozen=True, kw_only=True)
class EvidenceTestItem:
    requirement: str
    match: Match
    source: Source
    why: str
    quote: str | None = None
    record_id: uuid.UUID | None = None
    record_title: str | None = None


@dataclass(frozen=True, kw_only=True)
class TeamCheck:
    status: TeamStatus
    text: str


@dataclass(frozen=True, kw_only=True)
class EvidenceTestRecord:
    role_id: uuid.UUID
    verdict: Verdict
    verdict_text: str
    shown: int
    total: int
    from_records: int
    items: tuple[EvidenceTestItem, ...]
    team_check: TeamCheck
    checked_at: datetime
    model: str


class EvidenceStore(Protocol):
    async def latest(self, user_id: uuid.UUID, role_id: uuid.UUID) -> EvidenceTestRecord | None:
        """The role's latest evidence test, or None."""
        ...

    async def save(self, user_id: uuid.UUID, record: EvidenceTestRecord) -> None:
        """Replace the role's evidence test."""
        ...

    async def record_uses(self, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Per experience record: how many roles' stored tests show a must-have from it."""
        ...


# -- verbatim quotes


def _norm(text: str) -> str:
    """Whitespace runs collapsed and case folded: PDF line breaks aren't a misquote."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(frozen=True, kw_only=True)
class Sources:
    """The texts a quote may come from, by slot, and the records by id."""

    cv: dict[Source, str]
    records: dict[uuid.UUID, ExperienceEntryRecord]

    @classmethod
    def of(cls, profile: CandidateProfile, records: Sequence[ExperienceEntryRecord]) -> Sources:
        cv: dict[Source, str] = {}
        if profile.cv_technical:
            cv["cv_technical"] = profile.cv_technical
        if profile.cv_leadership:
            cv["cv_leadership"] = profile.cv_leadership
        return cls(cv=cv, records={record.id: record for record in records})

    def locate(
        self, quote: str, source: Source, record_id: str | None
    ) -> tuple[Source, ExperienceEntryRecord | None] | None:
        """Where `quote` really appears: the claimed place first, then any other CV or record."""
        wanted = _norm(quote)
        if not wanted:
            return None
        claimed = _record_id(record_id)
        order: list[tuple[Source, ExperienceEntryRecord | None]] = []
        if source in CV_SOURCES:
            order.append((source, None))
        named = self.records.get(claimed) if claimed is not None else None
        if source == "records" and named is not None:
            order.append(("records", named))
        order.extend((slot, None) for slot in CV_SOURCES if slot != source)
        records: Source = "records"
        order.extend((records, rec) for key, rec in self.records.items() if key != claimed)
        for slot, record in order:
            text = record_text(record) if record is not None else self.cv.get(slot, "")
            if text and wanted in _norm(text):
                return slot, record
        return None


def _record_id(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def record_title(record: ExperienceEntryRecord) -> str:
    """How a cited record is named: the requirement it answers."""
    return record.requirement[:300]


def record_text(record: ExperienceEntryRecord) -> str:
    """Everything a quote from the record may come from: the requirement and each entry."""
    return "\n".join([record.requirement, *(entry_line(entry) for entry in record.entries)])


def record_uses(tests: Iterable[Iterable[uuid.UUID]]) -> dict[uuid.UUID, int]:
    """Per record: how many evidence tests (one per role) show a must-have from it, given the
    record ids each test's `shown` items cite."""
    counts: dict[uuid.UUID, int] = {}
    for cited in tests:
        for record_id in set(cited):
            counts[record_id] = counts.get(record_id, 0) + 1
    return counts


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def check_item(answer: MustHaveAnswer, sources: Sources) -> EvidenceTestItem:
    """Keep a quote only where it really appears; downgrade an unbacked `shown` to `partial`."""
    requirement = _clip(answer.requirement, MAX_REQUIREMENT) or "Unnamed requirement"
    why = _clip(answer.why, MAX_WHY)
    if answer.match == "missing":
        return EvidenceTestItem(requirement=requirement, match="missing", source="none", why=why)
    found = sources.locate(answer.quote, answer.source, answer.record_id)
    if found is None:
        if answer.match == "shown":
            why = _clip(f"{NOT_BACKED} {why}", MAX_WHY)
        return EvidenceTestItem(requirement=requirement, match="partial", source="none", why=why)
    slot, record = found
    return EvidenceTestItem(
        requirement=requirement,
        match=answer.match,
        source=slot,
        why=why,
        quote=_clip(answer.quote, MAX_QUOTE),
        record_id=record.id if record is not None else None,
        record_title=record_title(record) if record is not None else None,
    )


# -- team check and verdict


def team_check(ad_team: int | None, largest_led: int | None) -> TeamCheck:
    if not ad_team:
        return TeamCheck(status="not_stated", text="Team: not stated.")
    if not largest_led:
        return TeamCheck(
            status="not_stated",
            text=f"Team: the ad mentions {ad_team}; your CV and records don't say how many "
            "you've led.",
        )
    if ad_team <= TEAM_FACTOR * largest_led:
        return TeamCheck(status="ok", text="Team: within twice what you've led.")
    return TeamCheck(
        status="over",
        text=f"Team: {ad_team} is more than twice the {largest_led} you've led.",
    )


def with_verdict(
    record: EvidenceTestRecord, score: int | None, keep_score: int
) -> EvidenceTestRecord:
    chosen = derive_verdict(
        record.total,
        record.shown,
        score,
        keep_score,
        team_over=record.team_check.status == "over",
    )
    text = verdict_text(chosen, record.total, record.shown, record.from_records)
    return replace(record, verdict=chosen, verdict_text=text)


def summary_of(record: EvidenceTestRecord, keep_score: int) -> dict[str, object]:
    """What `Role.evidence` reads (in `score_breakdown`), with the CV slots folded to `cv`."""
    return {
        "shown": record.shown,
        "total": record.total,
        "from_records": record.from_records,
        "team_over": record.team_check.status == "over",
        "keep_score": keep_score,
        "items": [
            {
                "requirement": item.requirement,
                "match": item.match,
                "source": "cv" if item.source in CV_SOURCES else item.source,
            }
            for item in record.items
        ],
    }


# -- the service


def evidence_block(
    profile: CandidateProfile, records: Sequence[ExperienceEntryRecord]
) -> DataBlock:
    values: dict[str, object] = {
        "spelling": profile.spelling,
        "cv_technical": profile.cv_technical,
        "cv_leadership": profile.cv_leadership,
        "extra_skills": profile.skills,
        "records": [
            {
                "id": str(record.id),
                "requirement": record.requirement,
                "entries": [
                    {"context": entry.context, "what_they_did": entry.detail}
                    for entry in record.entries
                ],
            }
            for record in records
        ],
    }
    present = {key: value for key, value in values.items() if value not in (None, "", [])}
    return DataBlock("candidate_evidence", json.dumps(present, ensure_ascii=False, indent=1))


def _listing_block(role: RoleRecord) -> DataBlock:
    listing = {"title": role.title, "company": role.company, "location": role.location}
    present = {key: value for key, value in listing.items() if value}
    return DataBlock("listing", json.dumps(present, ensure_ascii=False, sort_keys=True))


@dataclass(kw_only=True)
class EvidenceTestService:
    role_service: RoleService
    roles: RoleStore
    profiles: ProfileStore
    experience: ExperienceStore
    store: EvidenceStore
    llm: LLM
    clock: Clock
    prefs: PrefsSource | None = None

    async def _keep_score(self, user_id: uuid.UUID) -> int:
        prefs = DEFAULTS if self.prefs is None else await self.prefs.for_user(user_id)
        return prefs.scoring.keep_score

    async def latest(self, principal: Principal, role_id: uuid.UUID) -> EvidenceTestRecord:
        """Raises `NotFound` for another user's role or one never checked."""
        role = await self.role_service.get_role(principal, role_id)
        found = await self.store.latest(principal.user_id, role_id)
        if found is None:
            raise NotFound("This role hasn't had an evidence test yet.")
        return with_verdict(found, role.score, await self._keep_score(principal.user_id))

    async def run(self, principal: Principal, role_id: uuid.UUID) -> EvidenceTestRecord:
        """Raises `NotFound`, `ValidationFailed` (no ad text), `GuardBlocked`, `BudgetExceeded`
        or `UpstreamUnavailable`."""
        user_id = principal.user_id
        role = await self.role_service.get_role(principal, role_id)
        if not (role.description or "").strip():
            raise ValidationFailed("This role has no ad text to check. Paste the ad first.")
        profile = await self.profiles.load_profile(user_id)
        records = await self.experience.list_records(user_id, MAX_RECORDS)
        request = LlmRequest(
            feature=LlmFeature.EVIDENCE,
            system=EVIDENCE_SYSTEM,
            instructions=EVIDENCE_INSTRUCTIONS,
            attribution=LlmAttribution(user_id=user_id, role_id=role_id),
            data=(
                evidence_block(profile, records),
                _listing_block(role),
                DataBlock("job_ad", (role.description or "")[:MAX_AD_CHARS]),
            ),
        )
        result = await self.llm.generate(request, EvidenceAnswer)
        sources = Sources.of(profile, records)
        items = tuple(check_item(answer, sources) for answer in result.value.must_haves)
        items = items[:MAX_ITEMS]
        shown = [item for item in items if item.match == "shown"]
        keep = await self._keep_score(user_id)
        record = with_verdict(
            EvidenceTestRecord(
                role_id=role_id,
                verdict="skip",
                verdict_text="",
                shown=len(shown),
                total=len(items),
                from_records=sum(1 for item in shown if item.source == "records"),
                items=items,
                team_check=team_check(result.value.ad_team_size, result.value.largest_team_led),
                checked_at=self.clock.now(),
                model=result.model[:100],
            ),
            role.score,
            keep,
        )
        await self.store.save(user_id, record)
        breakdown = {**(role.score_breakdown or {}), SUMMARY_KEY: summary_of(record, keep)}
        values: dict[str, object] = {"score_breakdown": breakdown, "updated_at": record.checked_at}
        write = RoleWrite(role_id=role_id, version=None, values=values)
        await self.roles.write_role(user_id, write)
        return record
