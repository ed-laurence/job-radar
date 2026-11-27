"""The ``/out/triage.json`` contract of a triage run (stdlib only).

The backend parses this file strictly (unknown keys rejected), so the triager's
``submit_triage`` tool validates the model's answer here before anything is written, and
returns every problem as a tool error the model can fix. ``validate`` also fills the optional
fields so the file always carries every key::

    {"task_id", "type": feature|bug|security|performance|chore|refactor|test|docs,
     "size": S|M|L, "risk": low|medium|high, "areas": [web|api] (unique, <=2; >=1 in scope),
     "priority": P0..P3, "severity": critical|major|minor|trivial, "impact" <=600,
     "route": now|next|later, "route_reason" <=400, "needs_human_design": bool,
     "out_of_scope": bool, "out_of_scope_reason" <=1000 (exactly when out of scope),
     "likely_paths": [<=300] (<=30),
     "verification": {"status": confirmed|not_reproduced|unclear|duplicate,
                      "evidence" <=1000, "code_refs": [<=300] (<=20),
                      "duplicate_of": open-card id | null (set exactly for duplicate)}}

Triage writes no acceptance criteria: the planner does. With ``repo`` (the read-only checkout)
every ``code_refs`` path must exist (file or directory) and every ``likely_paths`` entry must
exist or be a new file in an existing directory. With ``open_cards`` (the ids get_task listed)
``duplicate_of`` must be one of them. The backend re-checks the paths and the card itself.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from plan_contract import Checker, show

TOP_KEYS = (
    "task_id",
    "type",
    "size",
    "risk",
    "areas",
    "priority",
    "severity",
    "impact",
    "route",
    "route_reason",
    "needs_human_design",
    "out_of_scope",
    "out_of_scope_reason",
    "likely_paths",
    "verification",
)
VERIFICATION_KEYS = ("status", "evidence", "code_refs", "duplicate_of")
TYPES = ("feature", "bug", "security", "performance", "chore", "refactor", "test", "docs")
SIZES = ("S", "M", "L")
RISKS = ("low", "medium", "high")
AREAS = ("web", "api")
PRIORITIES = ("P0", "P1", "P2", "P3")
SEVERITIES = ("critical", "major", "minor", "trivial")
ROUTES = ("now", "next", "later")
STATUSES = ("confirmed", "not_reproduced", "unclear", "duplicate")
MAX_IMPACT, MAX_ROUTE_REASON, MAX_REASON, MAX_EVIDENCE = 600, 400, 1000, 1000
MAX_PATH, MAX_LIKELY_PATHS, MAX_CODE_REFS = 300, 30, 20
MAX_ERRORS = 15


class TriageError(Exception):
    """The triage breaks the contract; ``errors`` are short, fixable messages."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)[:MAX_ERRORS]
        super().__init__("triage refused: " + "; ".join(self.errors))


def as_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _choice(c: Checker, where: str, value: object, allowed: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        c.err(f"{where} must be one of {', '.join(allowed)}")
        return ""
    return value


def _flag(c: Checker, where: str, value: object) -> bool:
    if not isinstance(value, bool):
        c.err(f"{where} must be true or false")
        return False
    return value


def _areas(c: Checker, value: object, out_of_scope: bool) -> list[str]:
    if not isinstance(value, list):
        c.err("areas must be a list of 'web' and/or 'api'")
        return []
    items = cast("list[object]", value)
    areas = [_choice(c, f"areas[{i}]", item, AREAS) for i, item in enumerate(items)]
    if len(set(areas)) != len(areas):
        c.err("areas must not repeat")
    if not out_of_scope and not items:
        c.err("an in-scope card names at least one area (web or api)")
    return [a for a in areas if a]


def _paths(c: Checker, where: str, value: object, hi: int, *, must_exist: bool) -> list[str]:
    paths = c.strings(where, value, 0, hi, MAX_PATH)
    for i, path in enumerate(paths):
        rel = c.rel_path(f"{where}[{i}]", path)
        if rel is None or c.repo is None:
            continue
        if must_exist and not (c.repo / rel).exists():
            c.err(f"{where}[{i}]: {show(rel)} does not exist in the repo; cite existing paths")
        elif not must_exist:
            c.parent_exists(f"{where}[{i}]", rel, "the")
    return paths


def _duplicate_of(
    c: Checker, value: object, status: str, open_cards: frozenset[str] | None
) -> str | None:
    if value is None:
        if status == "duplicate":
            c.err("verification.duplicate_of must name the open card this one duplicates")
        return None
    if status != "duplicate":
        c.err("verification.duplicate_of is only for status 'duplicate'; use null")
        return None
    try:
        card = str(uuid.UUID(str(value))) if isinstance(value, str) else ""
    except ValueError:
        card = ""
    if not card:
        c.err("verification.duplicate_of must be a card id from open_cards")
        return None
    if open_cards is not None and card not in open_cards:
        c.err(f"verification.duplicate_of {show(card)} is not one of the open cards")
        return None
    return card


def _verification(c: Checker, value: object, open_cards: frozenset[str] | None) -> object:
    if not isinstance(value, dict):
        c.err("verification must be an object {status, evidence, code_refs, duplicate_of}")
        return None
    raw = cast("dict[str, object]", value)
    c.keys("verification", raw, VERIFICATION_KEYS)
    status = _choice(c, "verification.status", raw.get("status"), STATUSES)
    return {
        "status": status,
        "evidence": c.text("verification.evidence", raw.get("evidence"), MAX_EVIDENCE),
        "code_refs": _paths(
            c, "verification.code_refs", raw.get("code_refs", []), MAX_CODE_REFS, must_exist=True
        ),
        "duplicate_of": _duplicate_of(c, raw.get("duplicate_of"), status, open_cards),
    }


def _scope_reason(c: Checker, out_of_scope: bool, value: object) -> str | None:
    if out_of_scope:
        return c.text("out_of_scope_reason", value, MAX_REASON) or None
    if value is not None:
        c.err("out_of_scope_reason is only for out-of-scope cards; use null")
    return None


def validate(
    raw: object,
    task_id: str,
    repo: Path | None = None,
    open_cards: frozenset[str] | None = None,
) -> dict[str, object]:
    """The triage in contract shape (optional fields filled), or ``TriageError``."""
    c = Checker(repo)
    if not isinstance(raw, dict):
        raise TriageError(["the triage must be a JSON object"])
    data = cast("dict[str, object]", raw)
    c.keys("triage", data, TOP_KEYS)
    if data.get("task_id", task_id) != task_id:
        c.err(f"task_id must be {task_id}")
    out_of_scope = _flag(c, "out_of_scope", data.get("out_of_scope"))
    triage: dict[str, object] = {
        "task_id": task_id,
        "type": _choice(c, "type", data.get("type"), TYPES),
        "size": _choice(c, "size", data.get("size"), SIZES),
        "risk": _choice(c, "risk", data.get("risk"), RISKS),
        "areas": _areas(c, data.get("areas"), out_of_scope),
        "priority": _choice(c, "priority", data.get("priority"), PRIORITIES),
        "severity": _choice(c, "severity", data.get("severity"), SEVERITIES),
        "impact": c.text("impact", data.get("impact"), MAX_IMPACT),
        "route": _choice(c, "route", data.get("route"), ROUTES),
        "route_reason": c.text("route_reason", data.get("route_reason"), MAX_ROUTE_REASON),
        "needs_human_design": _flag(c, "needs_human_design", data.get("needs_human_design")),
        "out_of_scope": out_of_scope,
        "out_of_scope_reason": _scope_reason(c, out_of_scope, data.get("out_of_scope_reason")),
        "likely_paths": _paths(
            c, "likely_paths", data.get("likely_paths", []), MAX_LIKELY_PATHS, must_exist=False
        ),
        "verification": _verification(c, data.get("verification"), open_cards),
    }
    if c.errors:
        raise TriageError(c.errors)
    return triage


def summary(triage: Mapping[str, object]) -> str:
    """One line from the enums and counts only (the free text is untrusted)."""
    verification = as_dict(triage.get("verification"))
    refs = verification.get("code_refs")
    count = len(cast("list[object]", refs)) if isinstance(refs, list) else 0
    scope = "out of scope" if triage.get("out_of_scope") is True else "in scope"
    return (
        f"{triage.get('type')}, {triage.get('priority')}/{triage.get('severity')}, "
        f"route {triage.get('route')}, {verification.get('status')} "
        f"({count} code ref{'' if count == 1 else 's'}), {scope}"
    )


def write(triage: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(triage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
