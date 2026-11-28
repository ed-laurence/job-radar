"""The task-chat tools the model sees, and the text they return. Every tool works on the one
card the chat is about: none takes a card id, so no other card is reachable.

Each tool's input is a Pydantic model (`extra="forbid"`, every property required, nullable for
"leave as is"): it is both the strict JSON schema the model sees and the validation of what it
sends (ARCH-21). Results are untrusted data for the next turn (the adapter guards them).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.devboard.model import PlanRecord, TaskRecord, TriageRecord
from app.domain.devboard.proposals import (
    MAX_BODY,
    MAX_CRITERIA,
    MAX_CRITERION,
    MAX_REASON,
    MAX_TITLE,
    EditChanges,
    split_body,
)
from app.domain.ports import ToolSpec

MAX_RESULT_CHARS = 6000
GET_TASK, GET_TRIAGE, GET_PLAN, PROPOSE = "get_task", "get_triage", "get_plan", "propose_task_edit"


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(max_length=MAX_TITLE, description="New title, or null.")
    description: str | None = Field(
        max_length=MAX_BODY,
        description="New description (the body without its acceptance criteria), or null.",
    )
    acceptance_criteria: list[str] | None = Field(
        max_length=MAX_CRITERIA,
        description="The full new list of acceptance criteria, one short checkable statement "
        f"each (at most {MAX_CRITERION} characters), or null.",
    )
    reason: str = Field(min_length=1, max_length=MAX_REASON, description="Why, in one sentence.")


_NO_INPUT = NoInput.model_json_schema()
TOOLS = (
    ToolSpec(
        GET_TASK,
        "Read the card: title, column, description, acceptance criteria and labels.",
        _NO_INPUT,
    ),
    ToolSpec(
        GET_TRIAGE,
        "Read the card's latest triage: type, size, risk, areas, scope, priority, severity, "
        "route and how the issue was verified.",
        _NO_INPUT,
    ),
    ToolSpec(
        GET_PLAN,
        "Read the card's latest work plan: subtasks, lease paths, acceptance tests and approval.",
        _NO_INPUT,
    ),
    ToolSpec(
        PROPOSE,
        "Propose one edit to the card for the person to accept or reject. Pass only the fields "
        "that change; null leaves a field as it is. It does not change the card.",
        ProposeInput.model_json_schema(),
    ),
)

STEP_SUMMARIES = {
    GET_TASK: "Read the card.",
    GET_TRIAGE: "Read the triage.",
    GET_PLAN: "Read the plan.",
}


def task_text(task: TaskRecord) -> str:
    description, criteria = split_body(task.body)
    lines = [
        f"Title: {task.title}",
        f"Column: {task.column.value}",
        f"Description:\n{description or '(none)'}",
        "Acceptance criteria (written on the card):",
        *([f"- {item}" for item in criteria] or ["(none)"]),
    ]
    if task.labels:
        lines.append(f"Labels: {', '.join(task.labels)}")
    return _clip("\n".join(lines))


def triage_text(triage: TriageRecord | None) -> str:
    if triage is None:
        return "Not triaged yet."
    areas = "+".join(area.value for area in triage.areas) or "none"
    scope = "in scope" if triage.in_scope else f"out of scope: {triage.out_of_scope_reason}"
    verification = triage.verification
    lines = [
        f"{triage.type.value}, size {triage.size.value}, risk {triage.risk.value}, "
        f"areas {areas}, {scope}.",
        f"Priority {triage.priority.value}, severity {triage.severity.value}; "
        f"route {triage.route.value}: {triage.route_reason or '(no reason)'}",
        f"Impact: {triage.impact or '(not stated)'}",
        f"Verification: {verification.status.value}. {verification.evidence}".strip(),
        f"Needs a human design decision: {'yes' if triage.needs_human_design else 'no'}.",
    ]
    if verification.code_refs:
        lines.append(f"Code it concerns: {', '.join(verification.code_refs)}")
    return _clip("\n".join(lines))


def plan_text(plan: PlanRecord | None) -> str:
    if plan is None:
        return "No plan yet."
    approval = "approved" if plan.is_approved else "not approved yet"
    lines = [
        f"Plan v{plan.version}, {plan.mode} agent, risk {plan.risk.value}, {approval}.",
        f"Budget: ${plan.budget_usd:.2f}, {plan.budget_minutes} minutes.",
        "Subtasks:",
        *[
            f"- {sub.title} ({sub.agent}; paths: {', '.join(sub.lease_paths) or 'none'})"
            for sub in plan.subtasks
        ],
        "Acceptance criteria (written by the planner):",
        *(
            [f"- {item.test_id}: {item.expectation}" for item in plan.acceptance_criteria]
            or ["(none)"]
        ),
        "Acceptance tests:",
        *([f"- {test}" for test in plan.acceptance_tests] or ["(none yet)"]),
    ]
    return _clip("\n".join(lines))


def plan_summary(plan: PlanRecord | None) -> str:
    if plan is None:
        return "No plan yet."
    approval = "approved" if plan.is_approved else "not approved yet"
    return f"Plan v{plan.version}: {len(plan.subtasks)} subtasks, {approval}."


def proposed_text(changes: EditChanges) -> str:
    return (
        f"Proposal saved for the person to accept or reject. It changes the "
        f"{', '.join(changes.field_names)}. The card itself is unchanged."
    )


def _clip(text: str) -> str:
    return text if len(text) <= MAX_RESULT_CHARS else text[: MAX_RESULT_CHARS - 1] + "…"
