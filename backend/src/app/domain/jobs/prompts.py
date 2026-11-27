"""Prompt material shared by scoring, kits, chat, rejections and follow-ups (ARCH-22).

System prompts are fixed text with no user data. Everything about the user and the role goes in
`DataBlock`s: the candidate profile (the caller's own records) and the listing and ad (untrusted).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.domain.jobs.records import CandidateProfile, RoleRecord
from app.domain.ports import DataBlock

MAX_AD_CHARS = 30_000

VOICE_RULES = (
    "Write plainly and warmly, with contractions. Use the spelling given in the candidate "
    "profile (`spelling`). No em or en dashes and no stock phrases (passionate, fast-paced, "
    "team player, leverage, synergy, I hope this finds you well)."
)
FACT_RULES = (
    "Facts about the candidate come only from the candidate profile. Never invent employers, "
    "numbers, technologies, qualifications or outcomes. Facts about the role come only from the "
    "listing and the ad."
)


def _compact(values: Mapping[str, object]) -> str:
    present = {key: value for key, value in values.items() if value not in (None, "", [], ())}
    return json.dumps(present, ensure_ascii=False, indent=1, sort_keys=True, default=str)


def profile_block(profile: CandidateProfile) -> DataBlock:
    experience = [
        {"requirement": item.requirement, "evidence": list(item.details)}
        for item in profile.experience
    ]
    values: dict[str, object] = {
        "name": profile.name,
        "headline": profile.headline,
        "home_location": profile.location,
        "country": profile.country,
        "spelling": profile.spelling,
        "skills_and_experience": profile.skills,
        "looking_for": profile.brief,
        "experience": experience,
        "lessons_from_past_rejections": list(profile.lessons),
        "cv_technical": profile.cv_technical,
        "cv_leadership": profile.cv_leadership,
        "linkedin_profile": profile.linkedin_profile,
    }
    return DataBlock("candidate_profile", _compact(values))


def listing_block(role: RoleRecord) -> DataBlock:
    values: dict[str, object] = {
        "title": role.title,
        "company": role.company,
        "location": role.location,
        "work_mode": role.work_mode,
        "pay": role.pay,
        "posted_at": role.posted_at.isoformat() if role.posted_at else None,
        "url": role.url,
        "track": role.track,
        "mission": role.mission,
        "assessment_score": role.score,
        "assessment": role.why,
        "watch_outs": list(role.risks or ()),
    }
    return DataBlock("listing", _compact(values))


def ad_block(role: RoleRecord) -> DataBlock | None:
    if not role.description:
        return None
    return DataBlock("job_ad", role.description[:MAX_AD_CHARS])


def role_blocks(role: RoleRecord, profile: CandidateProfile) -> tuple[DataBlock, ...]:
    blocks = [profile_block(profile), listing_block(role)]
    ad = ad_block(role)
    if ad is not None:
        blocks.append(ad)
    return tuple(blocks)
