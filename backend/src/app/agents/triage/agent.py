"""Triage agent: guard -> PII -> structured LLM call -> repo path check -> decision. Every step
is stored and streamed through the run's tools. Code, not the model, then sets risk, scope and
the effective route (`app.domain.devboard.triage_policy`), and the workflow checks the paths
again against the same `RepoFiles`.

The model sees the repo listing and the other open cards (as refs `C1`, `C2`, ...). An answer
naming paths that aren't in the repo is asked again once with a `path_problems` block; a
second bad answer fails the run with `ValidationFailed` naming the paths.
"""

from __future__ import annotations

import uuid

from app.adapters.llm.providers.replay import ReplayFixtureMissing
from app.agents.triage.prompt import INSTRUCTIONS, SYSTEM
from app.agents.triage.schema import TriageOutput
from app.domain.devboard.model import (
    Area,
    NewStep,
    Priority,
    Risk,
    Route,
    Severity,
    Size,
    StepKind,
    TaskType,
    Verification,
    VerificationStatus,
)
from app.domain.devboard.repo_paths import repo_listing, unknown_paths
from app.domain.devboard.triage_policy import shown_paths
from app.domain.errors import GuardBlocked, UpstreamUnavailable, ValidationFailed
from app.domain.ports import (
    LLM,
    PII,
    ContentGuard,
    DataBlock,
    LlmAttribution,
    LlmFeature,
    LlmRequest,
    LlmResult,
)
from app.domain.ports.devboard import CardRef, RepoFiles, RunContext, RunTools, TriageDecision

AGENT = "triage"


def task_text(title: str, body: str) -> str:
    return f"Title: {title}\n\n{body}".strip()


def card_refs(cards: tuple[CardRef, ...]) -> dict[str, uuid.UUID]:
    return {f"C{index}": card.id for index, card in enumerate(cards, start=1)}


def open_cards_text(cards: tuple[CardRef, ...]) -> str:
    """One line per open card: `C1 | triaged | <title>`. No ids, so the prompt (and its replay
    key) depends only on what the model needs."""
    if not cards:
        return "(no other open cards)"
    lines = (
        f"C{index} | {card.column.value} | {' '.join(card.title.split())[:200]}"
        for index, card in enumerate(cards, start=1)
    )
    return "\n".join(lines)


def output_paths(output: TriageOutput) -> tuple[str, ...]:
    return (*output.verification.code_refs, *output.likely_paths)


def path_problems(output: TriageOutput, files: frozenset[str]) -> tuple[str, ...]:
    """Unknown paths in an in-scope answer. Out-of-scope answers may name paths the listing
    leaves out (e.g. `infra/`): the card goes to Out of scope either way."""
    if output.out_of_scope:
        return ()
    return unknown_paths(output_paths(output), files)


class TriageLlmAgent:
    def __init__(self, llm: LLM, guard: ContentGuard, pii: PII, repo_files: RepoFiles) -> None:
        self._llm = llm
        self._guard = guard
        self._pii = pii
        self._repo_files = repo_files

    async def triage(self, context: RunContext, tools: RunTools) -> TriageDecision:
        await tools.emit(NewStep(StepKind.STATUS, "Triage started.", AGENT))
        text = task_text(context.task.title, context.task.body)
        verdict = await self._guard.inspect_input(text)
        if verdict.is_blocked:
            await tools.emit(NewStep(StepKind.STATUS, "The safety filter blocked the text.", AGENT))
            raise GuardBlocked("The card's text was blocked by the safety filter.")
        masked = await self._pii.deidentify(text)
        await tools.emit(
            NewStep(StepKind.THOUGHT, "Text passed the safety filter; personal data masked.", AGENT)
        )
        files = await self._repo_files.paths()
        blocks = (
            DataBlock("task", masked),
            DataBlock("repo_files", repo_listing(files)),
            DataBlock("open_cards", open_cards_text(context.open_cards)),
        )
        result = await self._checked(context, tools, blocks, files)
        await tools.emit(NewStep(StepKind.TOOL_RESULT, _summary(result.value), AGENT, "llm.triage"))
        return _decision(result, card_refs(context.open_cards))

    async def _checked(
        self,
        context: RunContext,
        tools: RunTools,
        blocks: tuple[DataBlock, ...],
        files: frozenset[str],
    ) -> LlmResult[TriageOutput]:
        await tools.emit(
            NewStep(StepKind.TOOL_CALL, "Structured triage request.", AGENT, tool="llm.triage")
        )
        result = await self._ask(context, blocks)
        unknown = path_problems(result.value, files)
        if not unknown:
            return result
        await tools.emit(
            NewStep(StepKind.THOUGHT, "Named paths not in the repo; asking again.", AGENT)
        )
        problems = DataBlock("path_problems", "Not in the repo:\n" + "\n".join(unknown))
        result = await self._ask(context, (*blocks, problems))
        unknown = path_problems(result.value, files)
        if unknown:
            raise ValidationFailed(
                f"Triage named paths that are not in the repo: {shown_paths(unknown)}"
            )
        return result

    async def _ask(
        self, context: RunContext, blocks: tuple[DataBlock, ...]
    ) -> LlmResult[TriageOutput]:
        request = LlmRequest(
            feature=LlmFeature.TRIAGE,
            system=SYSTEM,
            instructions=INSTRUCTIONS,
            attribution=LlmAttribution(user_id=context.actor.user_id, run_id=context.run.id),
            data=blocks,
        )
        try:
            return await self._llm.generate(request, TriageOutput)
        except ReplayFixtureMissing:
            # Offline mode has recordings only for known cards; say so instead of crashing.
            raise UpstreamUnavailable("No offline recording for this card's triage.") from None


def _summary(output: TriageOutput) -> str:
    areas = "+".join(output.areas) or "none"
    scope = "out of scope" if output.out_of_scope else "in scope"
    return (
        f"{output.type}, size {output.size}, areas {areas}, {output.priority} "
        f"{output.severity}, {output.verification.status}, route {output.route}, {scope}."
    )


def _verification(output: TriageOutput, refs: dict[str, uuid.UUID]) -> Verification:
    found = output.verification
    duplicate_of = refs.get(found.duplicate_of) if found.duplicate_of else None
    if found.duplicate_of and duplicate_of is None:
        raise ValidationFailed(
            f"Triage named a duplicate that is not an open card: {found.duplicate_of}"
        )
    return Verification(
        status=VerificationStatus(found.status),
        evidence=found.evidence,
        code_refs=tuple(found.code_refs),
        duplicate_of=duplicate_of,
    )


def _decision(result: LlmResult[TriageOutput], refs: dict[str, uuid.UUID]) -> TriageDecision:
    output = result.value
    return TriageDecision(
        type=TaskType(output.type),
        size=Size(output.size),
        llm_risk=Risk(output.risk),
        areas=tuple(Area(area) for area in output.areas),
        priority=Priority(output.priority),
        severity=Severity(output.severity),
        impact=output.impact,
        route=Route(output.route),
        route_reason=output.route_reason,
        verification=_verification(output, refs),
        needs_human_design=output.needs_human_design,
        likely_paths=tuple(output.likely_paths),
        out_of_scope_reason=output.out_of_scope_reason if output.out_of_scope else None,
        model=result.model,
        provider=result.provider,
    )
