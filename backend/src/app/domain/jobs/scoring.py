"""AI scoring and extraction, ported from job-seeker's `scoring.py` without the persona.

The model proposes a breakdown; code clamps it to the caps, recomputes freshness from the Clock
and applies the keep rule (a relevance veto, a score bar, and the mission wildcard).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import DomainError
from app.domain.identity import Principal
from app.domain.jobs.evidence import SUMMARY_KEY
from app.domain.jobs.ports import NewRoleEvent, ProfileStore, RoleStore, RoleWrite
from app.domain.jobs.prompts import FACT_RULES, ad_block, profile_block
from app.domain.jobs.records import RoleRecord
from app.domain.jobs.roles import RoleService
from app.domain.ports import LLM, Clock, DataBlock, Ids, LlmAttribution, LlmFeature, LlmRequest
from app.domain.user_settings import (
    DEFAULT_WEIGHTS,
    DEFAULTS,
    WORK_MODES,
    PrefsSource,
    ScoreWeights,
    ScoringPreferences,
    ScoringPrefs,
)

CAPS = DEFAULT_WEIGHTS.as_caps()
# The default filter bar: the lower of the default keep (55) and borderline (45) scores. Each
# user's own bar comes from their scoring settings (`ScoringPrefs.filter_below`).
KEEP_SCORE = DEFAULTS.scoring.filter_below
UNKNOWN_FRESHNESS = 4

SCORE_SYSTEM = (
    "You assess one job ad for a job seeker against their candidate profile. Be strict and "
    "accurate: they act on your score quickly, and weak or wrongly located roles waste their time."
)
ASSESS_INSTRUCTIONS = f"""Assess the role in the listing and job ad against the candidate profile.

{FACT_RULES}

Fit depends on seniority: for leadership roles judge scope, team, budget, stakeholders and domain,
and let tech-stack mismatches cost little; for hands-on roles the stack matters more, but adjacent
technologies earn partial credit.

Work arrangement: read it from the ad text, not the board's labels. work_mode is "remote" only if
the role can be done fully remotely, "hybrid" for regular office days, "on_site" for office or site
based, "unclear" if the ad doesn't say.

Scoring (integers): skills 0-50, track_pay 0-25 (track fit and pay), location 0-15 (15 for remote
open to the candidate's country or an office in their home location; 8 if unclear; 0 for an office
elsewhere or remote restricted elsewhere), freshness 0-10 (code recalculates this; put 0).
- track: "contract" for hands-on roles, "exec" for leadership roles.
- mission: the cause (Climate, Research, Health, Charity, Justice, Anti-fraud, Animal welfare)
  only if the organisation's core purpose is that cause, else null.
- relevant: whether the kind of job fits the candidate's direction, regardless of score.
- reason: one or two plain sentences on what the role is and the deciding factor.
- risks: short watch-outs, one sentence each (eligibility, on-site days, pay unknown, gaps)."""
# The evidence guidance lives in the schema's field description, not the instructions, so the
# prompt hash (and every recorded replay fixture) stays the same; the field is optional.
EVIDENCE_GUIDE = (
    "The ad's top must-haves (at most five). For each: match is shown, partial or missing; "
    "source is cv when the CV proves it, records when only their other records do, else none."
)

# Added only when the user set pay floors, work modes or places, so default prompts are unchanged.
PREFERENCE_RULES = """

The scoring_preferences block holds the candidate's own targets. Pay below a stated floor (permanent
salary per year, or contract day rate) earns few track_pay points; a work mode outside
accepted_work_modes earns no location points; an office in one of preferred_locations counts as
their home location."""


def assess_instructions(
    weights: ScoreWeights = DEFAULT_WEIGHTS, *, with_preferences: bool = False
) -> str:
    """The assessment instructions for these weights. The defaults give `ASSESS_INSTRUCTIONS`
    exactly (so the prompt hash and every replay fixture are unchanged)."""
    text = ASSESS_INSTRUCTIONS
    if weights != DEFAULT_WEIGHTS:
        unclear = round(weights.location * 8 / 15)
        for old, new in (
            ("skills 0-50", f"skills 0-{weights.skills}"),
            ("track_pay 0-25", f"track_pay 0-{weights.track_pay}"),
            (
                "location 0-15 (15 for remote",
                f"location 0-{weights.location} ({weights.location} for remote",
            ),
            ("8 if unclear", f"{unclear} if unclear"),
            ("freshness 0-10", f"freshness 0-{weights.freshness}"),
        ):
            text = text.replace(old, new)
    return text + PREFERENCE_RULES if with_preferences else text


def preferences_block(prefs: ScoringPreferences) -> DataBlock | None:
    if not prefs.has_targets:
        return None
    values: dict[str, object] = {
        "min_permanent_salary_aud": prefs.perm_salary_aud,
        "min_contract_day_rate_aud": prefs.contract_day_rate_aud,
        "accepted_work_modes": list(prefs.work_modes),
        "preferred_locations": list(prefs.preferred_locations),
    }
    present = {key: value for key, value in values.items() if value not in (None, [])}
    return DataBlock("scoring_preferences", json.dumps(present, sort_keys=True))


EXTRACT_SYSTEM = "You extract structured listing fields from a job ad."
EXTRACT_INSTRUCTIONS = (
    "Extract the listing fields from the job ad. Copy values as written; use null for anything "
    'the ad doesn\'t state. work_mode is "remote", "hybrid", "on_site" or null.'
)


class Breakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: int = 0
    track_pay: int = 0
    location: int = 0
    freshness: int = 0


class MustHave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(max_length=200)
    match: Literal["shown", "partial", "missing"]
    source: Literal["cv", "records", "none"] = "none"


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track: Literal["contract", "exec"]
    mission: str | None = Field(default=None, max_length=100)
    breakdown: Breakdown
    relevant: bool
    reason: str = Field(max_length=2000)
    risks: list[str] = Field(default_factory=list[str], max_length=20)
    work_mode: Literal["remote", "hybrid", "on_site", "unclear"] = "unclear"
    evidence: list[MustHave] | None = Field(default=None, max_length=5, description=EVIDENCE_GUIDE)


class ListingFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=300)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    work_mode: Literal["remote", "hybrid", "on_site"] | None = None
    pay: str | None = Field(default=None, max_length=100)


@dataclass(frozen=True)
class ScoringTicket:
    role_id: uuid.UUID
    state: str
    requested_at: datetime


def freshness_points(posted_at: datetime | None, now: datetime, cap: int = 10) -> int:
    """Points for how new the ad is, out of `cap` (10 by default, scaled for other weights)."""
    points = _freshness_tenths(posted_at, now)
    return points if cap == 10 else round(points * cap / 10)


def _freshness_tenths(posted_at: datetime | None, now: datetime) -> int:
    if posted_at is None:
        return UNKNOWN_FRESHNESS
    age_hours = (now - posted_at).total_seconds() / 3600
    if age_hours < 1:
        return 10
    if age_hours < 4:
        return 7
    if age_hours < 24:
        return 4
    return 0


def _clean_mission(mission: str | None) -> str | None:
    if mission is None or mission.strip().lower() in {"", "null", "none"}:
        return None
    return mission.strip()


def scored_values(
    assessment: Assessment,
    posted_at: datetime | None,
    now: datetime,
    thresholds: ScoringPrefs = DEFAULTS.scoring,
    preferences: ScoringPreferences = DEFAULTS.scoring_preferences,
) -> dict[str, object]:
    """Role columns from an assessment: the breakdown clamped to the user's weights, code
    freshness, accepted work modes, and the keep rule (the user's filter bar: the lower of their
    keep and borderline scores)."""
    raw = assessment.breakdown.model_dump()
    caps = preferences.weights.as_caps()
    breakdown = {name: max(0, min(cap, int(raw[name]))) for name, cap in caps.items()}
    breakdown["freshness"] = freshness_points(posted_at, now, caps["freshness"])
    risks = [risk.strip()[:300] for risk in assessment.risks if risk.strip()]
    mode = assessment.work_mode
    if preferences.work_modes and mode in WORK_MODES and mode not in preferences.work_modes:
        breakdown["location"] = 0
        risks.append(f"Work mode ({mode.replace('_', ' ')}) isn't one you accept.")
    total = sum(breakdown.values())
    mission = _clean_mission(assessment.mission)
    # The model's relevance is a veto; a mission organisation skips the score bar, not the veto.
    keep = assessment.relevant and (total >= thresholds.filter_below or mission is not None)
    stored: dict[str, object] = dict(breakdown)
    if assessment.evidence:
        stored["evidence"] = [item.model_dump() for item in assessment.evidence]
    values: dict[str, object] = {
        "score": total,
        "score_breakdown": stored,
        "track": assessment.track,
        "mission": mission,
        "why": assessment.reason.strip(),
        "risks": risks[:20],
        "stage": "scored" if keep else "filtered",
        "filter_reason": None if keep else _filter_reason(is_relevant=assessment.relevant),
    }
    return values


def _filter_reason(*, is_relevant: bool) -> str:
    return "ad check: score below threshold" if is_relevant else "ad check: not relevant"


class ScoringService:
    def __init__(
        self,
        roles: RoleStore,
        role_service: RoleService,
        profiles: ProfileStore,
        llm: LLM,
        clock: Clock,
        ids: Ids,
        prefs: PrefsSource | None = None,
    ) -> None:
        self._prefs = prefs
        self._roles = roles
        self._role_service = role_service
        self._profiles = profiles
        self._llm = llm
        self._clock = clock
        self._ids = ids

    async def request_scoring(self, principal: Principal, role_id: uuid.UUID) -> ScoringTicket:
        await self._role_service.get_role(principal, role_id)
        return ScoringTicket(role_id=role_id, state="queued", requested_at=self._clock.now())

    async def score_role(self, principal: Principal, role_id: uuid.UUID) -> RoleRecord:
        role = await self._role_service.get_role(principal, role_id)
        role = await self._fill_from_ad(principal, role)
        profile = await self._profiles.load_profile(principal.user_id)
        listing = {
            "title": role.title,
            "company": role.company,
            "location": role.location,
            "work_mode": role.work_mode,
            "pay": role.pay,
            "posted_at": role.posted_at.isoformat() if role.posted_at else None,
        }
        prefs = DEFAULTS if self._prefs is None else await self._prefs.for_user(principal.user_id)
        preferences = prefs.scoring_preferences
        blocks = [profile_block(profile), DataBlock("listing", json.dumps(listing, sort_keys=True))]
        ad = ad_block(role)
        if ad is not None:
            blocks.append(ad)
        targets = preferences_block(preferences)
        if targets is not None:
            blocks.append(targets)
        request = LlmRequest(
            feature=LlmFeature.SCORE,
            system=SCORE_SYSTEM,
            instructions=assess_instructions(
                preferences.weights, with_preferences=targets is not None
            ),
            attribution=LlmAttribution(user_id=principal.user_id, role_id=role_id),
            data=tuple(blocks),
        )
        result = await self._llm.generate(request, Assessment)
        now = self._clock.now()
        scored = scored_values(result.value, role.posted_at, now, prefs.scoring, preferences)
        tested = (role.score_breakdown or {}).get(SUMMARY_KEY)
        if tested is not None:  # a rescore keeps the last full evidence test's summary
            scored["score_breakdown"] = {
                **cast("dict[str, object]", scored["score_breakdown"]),
                SUMMARY_KEY: tested,
            }
        values = {**scored, "updated_at": now}
        event = NewRoleEvent(
            id=self._ids.new_id(), at=now, kind="scored", to_value=str(values["score"])
        )
        write = RoleWrite(role_id=role_id, version=None, values=values, events=(event,))
        return await self._roles.write_role(principal.user_id, write)

    async def score_in_background(self, principal: Principal, role_id: uuid.UUID) -> None:
        """For queued scoring: a typed failure marks the role `error` instead of escaping."""
        try:
            await self.score_role(principal, role_id)
        except DomainError:
            values = {"stage": "error", "updated_at": self._clock.now()}
            await self._roles.write_role(
                principal.user_id, RoleWrite(role_id=role_id, version=None, values=values)
            )

    async def _fill_from_ad(self, principal: Principal, role: RoleRecord) -> RoleRecord:
        """Extraction: fill missing listing fields from the pasted ad. Never overwrites."""
        missing = [
            n for n in ("company", "location", "work_mode", "pay") if getattr(role, n) is None
        ]
        if not role.description or not missing:
            return role
        request = LlmRequest(
            feature=LlmFeature.EXTRACT,
            system=EXTRACT_SYSTEM,
            instructions=EXTRACT_INSTRUCTIONS,
            attribution=LlmAttribution(user_id=principal.user_id, role_id=role.id),
            data=(DataBlock("job_ad", role.description[:20_000]),),
        )
        fields = (await self._llm.generate(request, ListingFields)).value.model_dump()
        values = {name: fields[name] for name in missing if fields.get(name)}
        if not values:
            return role
        write = RoleWrite(role_id=role.id, version=None, values=values)
        return await self._roles.write_role(principal.user_id, write)
