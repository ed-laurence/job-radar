"""Triage agent: guard -> PII -> structured LLM call -> decision. Every step is stored and
streamed through the run's tools. Code, not the model, then sets risk and scope
(`app.domain.devboard.triage_policy`)."""

from __future__ import annotations

from app.adapters.llm.providers.replay import ReplayFixtureMissing
from app.agents.triage.prompt import INSTRUCTIONS, SYSTEM
from app.agents.triage.schema import TriageOutput
from app.domain.devboard.model import (
    Area,
    Criterion,
    NewStep,
    Risk,
    Size,
    StepKind,
    TaskType,
)
from app.domain.errors import GuardBlocked, UpstreamUnavailable
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
from app.domain.ports.devboard import RunContext, RunTools, TriageDecision

AGENT = "triage"


def task_text(title: str, body: str) -> str:
    return f"Title: {title}\n\n{body}".strip()


class TriageLlmAgent:
    def __init__(self, llm: LLM, guard: ContentGuard, pii: PII) -> None:
        self._llm = llm
        self._guard = guard
        self._pii = pii

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
        await tools.emit(
            NewStep(StepKind.TOOL_CALL, "Structured triage request.", AGENT, tool="llm.triage")
        )
        result = await self._ask(context, masked)
        await tools.emit(NewStep(StepKind.TOOL_RESULT, _summary(result.value), AGENT, "llm.triage"))
        return _decision(result)

    async def _ask(self, context: RunContext, masked: str) -> LlmResult[TriageOutput]:
        request = LlmRequest(
            feature=LlmFeature.TRIAGE,
            system=SYSTEM,
            instructions=INSTRUCTIONS,
            attribution=LlmAttribution(user_id=context.actor.user_id, run_id=context.run.id),
            data=(DataBlock("task", masked),),
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
        f"{output.type}, size {output.size}, areas {areas}, "
        f"{len(output.acceptance_criteria)} acceptance criteria, {scope}."
    )


def _decision(result: LlmResult[TriageOutput]) -> TriageDecision:
    output = result.value
    return TriageDecision(
        type=TaskType(output.type),
        size=Size(output.size),
        llm_risk=Risk(output.risk),
        areas=tuple(Area(area) for area in output.areas),
        criteria=tuple(Criterion(c.test_id, c.expectation) for c in output.acceptance_criteria),
        needs_human_design=output.needs_human_design,
        likely_paths=tuple(output.likely_paths),
        out_of_scope_reason=output.out_of_scope_reason if output.out_of_scope else None,
        model=result.model,
        provider=result.provider,
    )
