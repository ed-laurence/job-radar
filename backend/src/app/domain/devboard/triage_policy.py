"""Turn the triage agent's answer into the stored triage. Code decides, not the model:

- risk is the path-risk map over the paths the task is expected to touch, and the model's
  estimate can only raise it;
- a task that needs any path outside the agent write scope is out of scope, whatever the
  model said; the model can mark a task out of scope but never bring one back in.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.devboard.model import Area, Risk, TriageRecord, max_risk
from app.domain.devboard.paths import InvalidPath, code_risk, normalise, out_of_scope_paths
from app.domain.ports.devboard import TriageDecision

# When the model names no paths, an area still implies where the work lands.
AREA_DEFAULT_PATHS: dict[Area, tuple[str, ...]] = {
    Area.WEB: ("web/src/features/",),
    Area.API: ("backend/src/app/api/", "backend/src/app/domain/"),
}
MAX_REASON_CHARS = 1000


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


def finalise(task_id: uuid.UUID, decision: TriageDecision, now: datetime) -> TriageRecord:
    reason = scope_reason(decision)
    return TriageRecord(
        task_id=task_id,
        type=decision.type,
        size=decision.size,
        risk=triage_risk(decision),
        areas=decision.areas,
        criteria=decision.criteria,
        needs_human_design=decision.needs_human_design,
        in_scope=reason is None,
        out_of_scope_reason=reason,
        created_at=now,
        model=decision.model,
        provider=decision.provider,
    )
