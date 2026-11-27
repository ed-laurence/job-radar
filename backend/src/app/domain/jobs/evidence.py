"""The evidence strip on a role card: how many of the ad's must-haves the user can show.

Scoring stores the model's must-have list (`requirement`, `match`: shown/partial/missing,
`source`: cv/records/none) under `score_breakdown.evidence` (optional, so older replay
fixtures still parse). Only `shown` items count as shown; `from_records` are shown items whose
source is the user's records. Roles without it, such as the seeded ones, get a deterministic
estimate from the skills subscore (out of `SKILLS_CAP`) over five must-haves. A role with no
breakdown has no evidence.

Verdict (after job-seeker's evidence test): apply when at least `MIN_SHOWN` must-haves are
shown, at most one is missing and the score is at least `APPLY_SCORE`; consider when it
passes on evidence or on score alone; otherwise skip.

Once the full evidence test has run (`evidence_test.py`), its summary (`SUMMARY_KEY` in the
breakdown) wins: verified counts, and the full test's verdict rule (`derive_verdict`) against
the role's current score and the keep threshold the test ran with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

APPLY_SCORE = 70
CONSIDER_SCORE = 55
MIN_SHOWN = 3
MAX_MISSING = 1
SKILLS_CAP = 50
ESTIMATED_MUST_HAVES = 5

type Verdict = Literal["apply", "consider", "skip"]
MATCHES = frozenset({"shown", "partial", "missing"})
SOURCES = frozenset({"cv", "records", "none"})
MAX_ITEMS = 12


@dataclass(frozen=True, kw_only=True)
class EvidenceItemView:
    requirement: str
    match: str
    source: str


VERDICT_LABELS = {"apply": "Apply", "consider": "Consider", "skip": "Skip"}


@dataclass(frozen=True, kw_only=True)
class EvidenceView:
    must_haves_total: int
    shown: int
    from_records: int
    verdict: Verdict
    summary: str
    items: tuple[EvidenceItemView, ...] = ()


def verdict(total: int, shown: int, score: int | None) -> Verdict:
    points = score or 0
    passes = shown >= min(MIN_SHOWN, total) and total - shown <= MAX_MISSING
    if passes and points >= APPLY_SCORE:
        return "apply"
    if passes or points >= APPLY_SCORE or (points >= CONSIDER_SCORE and shown >= MIN_SHOWN - 1):
        return "consider"
    return "skip"


SUMMARY_KEY = "evidence_test"


def passes_evidence(total: int, shown: int) -> bool:
    """The full test's rule: every must-have shown, or all but one (and at least one)."""
    return total > 0 and shown >= max(1, total - 1)


def derive_verdict(
    total: int, shown: int, score: int | None, keep_score: int, *, team_over: bool = False
) -> Verdict:
    """The full test's verdict: apply on evidence (team not over) and score; consider on
    either; otherwise skip."""
    on_evidence = passes_evidence(total, shown) and not team_over
    on_score = (score or 0) >= keep_score
    if on_evidence and on_score:
        return "apply"
    if on_evidence or on_score:
        return "consider"
    return "skip"


def verdict_text(chosen: Verdict, total: int, shown: int, from_records: int) -> str:
    return _summary(chosen, total, shown, from_records)


def _summary(chosen: Verdict, total: int, shown: int, from_records: int) -> str:
    text = f"{VERDICT_LABELS[chosen]} · you show {shown} of {total} must-haves"
    if from_records:
        text += f" ({from_records} from your records, not on your CV)"
    return text


def build(
    total: int,
    shown: int,
    from_records: int,
    score: int | None,
    items: tuple[EvidenceItemView, ...] = (),
) -> EvidenceView | None:
    if total <= 0:
        return None
    chosen = verdict(total, shown, score)
    return EvidenceView(
        must_haves_total=total,
        shown=shown,
        from_records=from_records,
        verdict=chosen,
        summary=_summary(chosen, total, shown, from_records),
        items=items,
    )


def _item(row: Mapping[str, object]) -> EvidenceItemView | None:
    requirement = str(row.get("requirement") or "").strip()[:200]
    match, source = row.get("match"), row.get("source", "none")
    if not requirement or match not in MATCHES or source not in SOURCES:
        return None
    return EvidenceItemView(requirement=requirement, match=str(match), source=str(source))


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _from_test(score: int | None, stored: Mapping[str, object]) -> EvidenceView | None:
    raw = stored.get("items")
    rows: tuple[EvidenceItemView, ...] = ()
    if isinstance(raw, list | tuple):
        parsed = [
            _item(cast("Mapping[str, object]", item))
            for item in cast("Sequence[object]", raw)
            if isinstance(item, Mapping)
        ]
        rows = tuple(row for row in parsed if row is not None)[:MAX_ITEMS]
    total, shown = _count(stored.get("total")), _count(stored.get("shown"))
    if total <= 0:
        return None
    from_records = _count(stored.get("from_records"))
    keep = _count(stored.get("keep_score")) or APPLY_SCORE
    chosen = derive_verdict(total, shown, score, keep, team_over=stored.get("team_over") is True)
    return EvidenceView(
        must_haves_total=total,
        shown=shown,
        from_records=from_records,
        verdict=chosen,
        summary=_summary(chosen, total, shown, from_records),
        items=rows,
    )


def role_evidence(score: int | None, breakdown: Mapping[str, object] | None) -> EvidenceView | None:
    if not breakdown:
        return None
    tested = breakdown.get(SUMMARY_KEY)
    if isinstance(tested, Mapping):
        return _from_test(score, cast("Mapping[str, object]", tested))
    items = breakdown.get("evidence")
    if isinstance(items, list | tuple) and items:
        parsed = [
            _item(cast("Mapping[str, object]", item))
            for item in cast("Sequence[object]", items)
            if isinstance(item, Mapping)
        ]
        rows = tuple(row for row in parsed if row is not None)[:MAX_ITEMS]
        shown = [row for row in rows if row.match == "shown"]
        on_records = sum(1 for row in shown if row.source == "records")
        return build(len(rows), len(shown), on_records, score, rows)
    skills = breakdown.get("skills")
    if not isinstance(skills, int) or isinstance(skills, bool):
        return None
    ratio = max(0, min(skills, SKILLS_CAP)) / SKILLS_CAP
    shown = round(ratio * ESTIMATED_MUST_HAVES)
    return build(ESTIMATED_MUST_HAVES, shown, 0, score)
