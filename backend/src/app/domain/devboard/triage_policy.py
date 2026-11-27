"""Turn the triage agent's answer into the stored triage. Code decides, not the model:

- risk is the path-risk map over the paths the task is expected to touch, and the model's
  estimate can only raise it;
- a task that needs any path outside the agent write scope is out of scope, whatever the
  model said; the model can mark a task out of scope but never bring one back in;
- the route is capped by the verification and the design question (`effective_route`);
- every path the answer names must be in the repo, and a duplicate must name an open card
  (`triage_problem`), or the triage run fails and the card stays put.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.devboard.model import (
    Area,
    Risk,
    Route,
    TriageRecord,
    Verification,
    VerificationStatus,
    max_risk,
)
from app.domain.devboard.paths import InvalidPath, code_risk, normalise, out_of_scope_paths
from app.domain.devboard.repo_paths import unknown_paths
from app.domain.ports.devboard import TriageDecision

# When the model names no paths, an area still implies where the work lands.
AREA_DEFAULT_PATHS: dict[Area, tuple[str, ...]] = {
    Area.WEB: ("web/src/features/",),
    Area.API: ("backend/src/app/api/", "backend/src/app/domain/"),
}
MAX_REASON_CHARS = 1000
MAX_IMPACT_CHARS = 600
MAX_ROUTE_REASON_CHARS = 400
MAX_EVIDENCE_CHARS = 1000
MAX_CODE_REFS = 20
MAX_LIKELY_PATHS = 30
MAX_SHOWN_PATHS = 5
_ROUTE_ORDER: tuple[Route, ...] = (Route.NOW, Route.NEXT, Route.LATER)


def _paths(decision: TriageDecision) -> tuple[tuple[str, ...], list[str]]:
    clean: list[str] = []
    rejected: list[str] = []
    for raw in decision.likely_paths:
        try:
            clean.append(normalise(raw))
        except InvalidPath:
            rejected.append(raw[:80])
    if not clean:
        for area in decision.areas:
            clean.extend(AREA_DEFAULT_PATHS[area])
    return tuple(dict.fromkeys(clean)), rejected


def model_paths(decision: TriageDecision) -> tuple[str, ...]:
    """The model's own likely paths, normalised (no area defaults), at most 30."""
    clean: list[str] = []
    for raw in decision.likely_paths:
        try:
            clean.append(normalise(raw))
        except InvalidPath:
            continue
    return tuple(dict.fromkeys(clean))[:MAX_LIKELY_PATHS]


def scope_reason(decision: TriageDecision) -> str | None:
    paths, rejected = _paths(decision)
    outside = out_of_scope_paths(paths)
    reasons: list[str] = []
    if decision.out_of_scope_reason:
        reasons.append(decision.out_of_scope_reason.strip())
    if outside:
        reasons.append("Needs changes outside the agent write scope: " + ", ".join(outside[:5]))
    if rejected:
        reasons.append("Names paths that aren't concrete repo paths.")
    if not decision.areas:
        reasons.append("Touches neither the web app nor the API.")
    if not reasons:
        return None
    return " ".join(reasons)[:MAX_REASON_CHARS]


def triage_risk(decision: TriageDecision) -> Risk:
    paths, _ = _paths(decision)
    in_scope = tuple(path for path in paths if path not in out_of_scope_paths(paths))
    return max_risk(code_risk(in_scope), decision.llm_risk)


# ------------------------------------------------------------------ routing
@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str


def _at_most(route: Route, cap: Route) -> Route:
    return max(route, cap, key=_ROUTE_ORDER.index)


def _cap(decision: TriageDecision, *, in_scope: bool) -> tuple[Route, str] | None:
    """The strictest rule that applies, as (cap, why); None when the model's route stands."""
    status = decision.verification.status
    rules = (
        (not in_scope, Route.LATER, "Out of scope."),
        (status is VerificationStatus.DUPLICATE, Route.LATER, "Duplicate of an open card."),
        (status is VerificationStatus.NOT_REPRODUCED, Route.LATER, "The issue was not reproduced."),
        (status is VerificationStatus.UNCLEAR, Route.NEXT, "The issue could not be verified yet."),
        (decision.needs_human_design, Route.NEXT, "Needs a human design decision first."),
    )
    return next(((cap, why) for applies, cap, why in rules if applies), None)


def effective_route(decision: TriageDecision, *, in_scope: bool) -> RouteDecision:
    """The model's route, capped: out of scope, duplicate or not reproduced -> `later`;
    unclear or needs human design -> at most `next`. Who made the card doesn't matter."""
    model_reason = " ".join(decision.route_reason.split())
    cap = _cap(decision, in_scope=in_scope)
    if cap is None or _at_most(decision.route, cap[0]) is decision.route:
        return RouteDecision(decision.route, model_reason[:MAX_ROUTE_REASON_CHARS])
    route, why = cap
    reason = f"{why} (Suggested {decision.route.value}: {model_reason})" if model_reason else why
    return RouteDecision(_at_most(decision.route, route), reason[:MAX_ROUTE_REASON_CHARS])


# ------------------------------------------------------------------ repo checks
def shown_paths(paths: tuple[str, ...]) -> str:
    shown = ", ".join(path[:120] for path in paths[:MAX_SHOWN_PATHS])
    more = len(paths) - MAX_SHOWN_PATHS
    return f"{shown} (and {more} more)" if more > 0 else shown


def triage_paths(decision: TriageDecision) -> tuple[str, ...]:
    return (*decision.verification.code_refs, *decision.likely_paths)


def triage_problem(
    decision: TriageDecision,
    files: frozenset[str] | None,
    open_cards: Iterable[uuid.UUID],
) -> str | None:
    """Why the answer can't be stored, or None. `files` None skips the path check (a card out
    of scope may name paths outside the repo listing)."""
    if files is not None:
        unknown = unknown_paths(triage_paths(decision), files)
        if unknown:
            return f"Triage named paths that are not in the repo: {shown_paths(unknown)}"
    verification = decision.verification
    is_duplicate = verification.status is VerificationStatus.DUPLICATE
    if verification.duplicate_of is None:
        missing = "Triage called the card a duplicate without naming the card."
        return missing if is_duplicate else None
    if not is_duplicate:
        return "Triage named a duplicate card but did not call the card a duplicate."
    if verification.duplicate_of not in set(open_cards):
        return f"Triage named a duplicate that is not an open card: {verification.duplicate_of}"
    return None


# ------------------------------------------------------------------ the record
def _verification(verification: Verification) -> Verification:
    return replace(
        verification,
        evidence=verification.evidence.strip()[:MAX_EVIDENCE_CHARS],
        code_refs=tuple(dict.fromkeys(verification.code_refs))[:MAX_CODE_REFS],
    )


def finalise(task_id: uuid.UUID, decision: TriageDecision, now: datetime) -> TriageRecord:
    reason = scope_reason(decision)
    route = effective_route(decision, in_scope=reason is None)
    return TriageRecord(
        task_id=task_id,
        type=decision.type,
        size=decision.size,
        risk=triage_risk(decision),
        areas=decision.areas,
        priority=decision.priority,
        severity=decision.severity,
        impact=decision.impact.strip()[:MAX_IMPACT_CHARS],
        route=route.route,
        route_reason=route.reason,
        verification=_verification(decision.verification),
        likely_paths=model_paths(decision),
        needs_human_design=decision.needs_human_design,
        in_scope=reason is None,
        out_of_scope_reason=reason,
        created_at=now,
        model=decision.model,
        provider=decision.provider,
    )
