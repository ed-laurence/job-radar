"""The kit quality bar checked in code, ported from job-seeker's `kits.quality_problems`.

Persona-specific rules (one person's sign-off, employers and word counts) became profile-driven
ones: the sign-off uses the profile name, spelling checks follow the profile's `spelling`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.domain.jobs.records import CandidateProfile, KitAnswerRecord

BANNED = (
    "i hope this finds you",
    "passionate",
    "fast-paced",
    "fast paced",
    "i am writing to apply",
    "team player",
    "leverage",
    "synergy",
)
_US_SPELLING = re.compile(
    r"\b\w*(?:optimi|organi|analy|prioriti|utili|reali|recogni|speciali|moderni|standardi|"
    r"customi|summari|minimi|maximi|centrali|operationali|democrati|digiti)z"
    r"(?:e|es|ed|ing|ation|ations)\b|\b(?:behavior|color|favorite|labor|center|centers|catalog)\b",
    re.IGNORECASE,
)
_CLAIM_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s?(?:x\b|%)", re.IGNORECASE)
_RIGHT_QUOTE, _EM_DASH, _EN_DASH = chr(0x2019), chr(0x2014), chr(0x2013)
_WORD = re.compile(r"[\w'" + _RIGHT_QUOTE + r"-]+")
WORDS_MIN, WORDS_MAX = 250, 650
OUTREACH_MAX_CHARS = 300
_SPELLING_CHECKED = {"en-AU", "en-GB", "en-NZ"}


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _cover_note_problems(note: str, profile: CandidateProfile) -> list[str]:
    problems: list[str] = []
    words = word_count(note.replace("**", ""))
    if not WORDS_MIN <= words <= WORDS_MAX:
        problems.append(f"cover letter is {words} words; it must be {WORDS_MIN}-{WORDS_MAX}")
    if not re.match(r"^\s*Dear\b", note):
        problems.append('the cover letter must start with the salutation ("Dear ...,")')
    if profile.name:
        sign_off = re.compile(r"Kind regards,\s*\n\s*" + re.escape(profile.name) + r"\s*$")
        if not sign_off.search(note):
            problems.append(f'the cover letter must end with "Kind regards," and {profile.name}')
    return problems


def kit_problems(
    parts: Mapping[str, str],
    answers: Sequence[KitAnswerRecord],
    profile: CandidateProfile,
    sources: str,
) -> list[str]:
    """Problems with the generated parts. An empty list means the kit passes."""
    problems: list[str] = []
    note = parts.get("cover_note")
    if note is not None:
        problems += _cover_note_problems(note, profile)
    outreach = parts.get("outreach")
    if outreach is not None and len(outreach) > OUTREACH_MAX_CHARS:
        problems.append(f"outreach is {len(outreach)} characters; max {OUTREACH_MAX_CHARS}")
    if "answers" in parts and not answers:
        problems.append("answers is empty; give 3-5 screening answers")
    prose = " ".join([*parts.values(), *(answer.a for answer in answers)])
    lowered = prose.lower()
    banned = sorted({phrase for phrase in BANNED if phrase in lowered})
    if banned:
        problems.append("remove these phrases: " + ", ".join(f'"{p}"' for p in banned))
    if _EM_DASH in prose or _EN_DASH in prose:
        problems.append("no em or en dashes; use commas or full stops")
    if profile.spelling in _SPELLING_CHECKED:
        found = sorted({hit.group(0).lower() for hit in _US_SPELLING.finditer(prose)})
        if found:
            problems.append("use British/Australian spelling instead of: " + ", ".join(found))
    source_text = sources.lower().replace(" ", "")
    claims = (note or "") + " " + (parts.get("cv") or "")
    unknown = sorted(
        {
            hit.group(0)
            for hit in _CLAIM_NUMBER.finditer(claims)
            if hit.group(0).lower().replace(" ", "") not in source_text
        }
    )
    if unknown:
        problems.append("these figures aren't in the profile or the ad: " + ", ".join(unknown))
    return problems


def parse_answers(text: str) -> list[KitAnswerRecord]:
    """`Q: ...` / `A: ...` blocks into answers. Lines without a marker continue the last one."""
    answers: list[KitAnswerRecord] = []
    question: str | None = None
    answer_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("Q:"):
            if question is not None:
                answers.append(_answer(question, answer_lines))
            question, answer_lines = stripped[2:].strip(), []
        elif stripped.upper().startswith("A:"):
            answer_lines.append(stripped[2:].strip())
        elif stripped and question is not None:
            answer_lines.append(stripped)
    if question is not None:
        answers.append(_answer(question, answer_lines))
    return [answer for answer in answers if answer.q and answer.a][:20]


def _answer(question: str, lines: Sequence[str]) -> KitAnswerRecord:
    return KitAnswerRecord(q=question[:500], a=" ".join(lines).strip()[:4000])
