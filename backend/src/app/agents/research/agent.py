"""The company-research agent: a visible, multi-step tool-use loop (ported from job-seeker's
`targets.research`) on the LLM port's native tool use (ARCH-20).

The agent owns the loop (the manual-loop pattern): each turn the model either calls one tool or
gives its final answer, validated by `ResearchAnswer` (ARCH-21). The agent runs the tool, adds
the call and its output to the transcript (the adapter guards outputs before the next turn,
SEC-04) and persists every model turn, tool call, server-tool search and tool result summary,
redacted, to the shared step stream. It stops at `max_turns` or when the run's budget is spent.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.agents.research.tools import ResearchTool, ToolResult
from app.domain.errors import DomainError
from app.domain.jobs.ports import Redaction, ResearchStore
from app.domain.jobs.records import ResearchRunRecord, StepRecord, TargetRecord
from app.domain.ports import (
    LLM,
    Clock,
    DataBlock,
    FinalAnswer,
    Ids,
    LlmAttribution,
    LlmFeature,
    LlmRequest,
    ServerTool,
    ServerToolUse,
    ToolCall,
    ToolOutput,
    ToolTurn,
    TranscriptItem,
)

AGENT = "research"
MAX_SOURCES = 50

SYSTEM = (
    "You research one organisation for a job seeker who is deciding whether and how to approach "
    "it. Work in short steps: call one tool at a time, read its result, then decide. Before each "
    "call, say in one short sentence what you're doing and why. Finish when you can give a "
    "useful, honest summary; say what you couldn't find."
)
INSTRUCTIONS = """Research the organisation in the `target` data block.

Focus areas: {focus}. You have at most {max_turns} turns, so use the tools sparingly.
When you have enough, stop calling tools and answer with:
- summary: a few short paragraphs covering the focus areas.
- findings: up to five one-line findings.
- sources: URLs from tool results that back the summary (never invent one)."""


@dataclass(frozen=True)
class ResearchConfig:
    """Per-run caps (model turns, and spend across them in USD) and any server tools on offer
    (web search, only when `RESEARCH_WEB_SEARCH` is on)."""

    max_turns: int = 6
    budget_usd: float = 0.50
    server_tools: tuple[ServerTool, ...] = ()


class ResearchAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=8000)
    findings: list[str] = Field(default_factory=list[str], max_length=5)
    sources: list[str] = Field(default_factory=list[str], max_length=MAX_SOURCES)


@dataclass
class _Progress:
    transcript: list[TranscriptItem] = field(default_factory=list[TranscriptItem])
    known_sources: set[str] = field(default_factory=set[str])
    spent_usd: float = 0.0


class ResearchAgent:
    def __init__(
        self,
        llm: LLM,
        store: ResearchStore,
        tools: Sequence[ResearchTool],
        redaction: Redaction,
        clock: Clock,
        ids: Ids,
        config: ResearchConfig | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._tools: Mapping[str, ResearchTool] = {tool.spec.name: tool for tool in tools}
        self._redaction = redaction
        self._clock = clock
        self._ids = ids
        self._limits = config or ResearchConfig()

    async def run(self, user_id: uuid.UUID, run: ResearchRunRecord, target: TargetRecord) -> None:
        steps = _StepWriter(self._store, self._redaction, self._clock, self._ids, user_id, run.id)
        await self._store.update_run(
            user_id, run.id, {"status": "running", "started_at": self._clock.now()}
        )
        await steps.write("status", "Started researching the organisation.")
        progress = _Progress()
        try:
            outcome = await self._loop(user_id, run, target, steps, progress)
        except DomainError as error:
            outcome = error.detail or "the model is unavailable."
        if isinstance(outcome, str):
            await steps.write("status", f"Stopped: {outcome}")
            await self._finish(user_id, run.id, "failed", None, (), progress)
            return
        for finding in outcome.findings:
            await steps.write("finding", finding)
        # Only URLs a tool actually returned count as sources: the model can't invent one.
        sources = tuple(url for url in outcome.sources if url in progress.known_sources)
        summary = await self._redaction.redact_step(outcome.summary)
        await steps.write("status", "Finished.")
        await self._finish(user_id, run.id, "succeeded", summary, sources[:MAX_SOURCES], progress)

    async def _loop(
        self,
        user_id: uuid.UUID,
        run: ResearchRunRecord,
        target: TargetRecord,
        steps: _StepWriter,
        progress: _Progress,
    ) -> ResearchAnswer | str:
        """The answer, or why the run stopped without one."""
        request = self._request(user_id, run, target)
        tools = (*(tool.spec for tool in self._tools.values()), *self._limits.server_tools)
        for _ in range(self._limits.max_turns):
            turn = ToolTurn(tools=tools, transcript=tuple(progress.transcript))
            action = await self._llm.next_action(request, turn, ResearchAnswer)
            progress.spent_usd += action.usage.cost_usd if action.usage else 0.0
            await self._record_turn(action, steps, progress)
            if isinstance(action, FinalAnswer):
                return action.value
            output = await self._call_tool(user_id, target, action, steps, progress)
            progress.transcript += [action, output]
            if progress.spent_usd >= self._limits.budget_usd:
                return "the research budget for this run is used up."
        return f"reached the limit of {self._limits.max_turns} model turns."

    async def _record_turn(
        self,
        action: ToolCall | FinalAnswer[ResearchAnswer],
        steps: _StepWriter,
        progress: _Progress,
    ) -> None:
        if isinstance(action, ToolCall):
            await steps.write("thought", action.text.strip() or f"Calling {action.name}.")
        else:
            await steps.write("thought", "Writing up what I found.")
        for use in action.server_uses:
            await self._record_server_use(use, steps, progress)

    async def _record_server_use(
        self, use: ServerToolUse, steps: _StepWriter, progress: _Progress
    ) -> None:
        """Server tools (web search) ran inside the turn; their results are shown, not run."""
        await steps.write("tool_call", f"{use.name}: {use.query}", tool=use.name)
        titles = "; ".join(use.titles[:5]) or "no results"
        await steps.write(
            "tool_result", f"{len(use.sources)} results: {titles}"[:300], tool=use.name
        )
        progress.known_sources.update(use.sources)

    async def _call_tool(
        self,
        user_id: uuid.UUID,
        target: TargetRecord,
        call: ToolCall,
        steps: _StepWriter,
        progress: _Progress,
    ) -> ToolOutput:
        tool = self._tools.get(call.name)
        shown = ", ".join(f"{key}={value}" for key, value in call.arguments.items())
        await steps.write("tool_call", f"{call.name}: {shown}"[:300], tool=call.name)
        if tool is None:
            result = ToolResult(f"There is no tool called {call.name[:64]!r}.", is_error=True)
        else:
            result = await tool.run(user_id, target, call.arguments)
        first_line = result.text.splitlines()[0] if result.text else "No result."
        await steps.write("tool_result", first_line[:300], tool=call.name)
        progress.known_sources.update(result.sources)
        return ToolOutput(call.id, result.text, result.is_error)

    def _request(
        self, user_id: uuid.UUID, run: ResearchRunRecord, target: TargetRecord
    ) -> LlmRequest:
        instructions = INSTRUCTIONS.format(
            focus=", ".join(run.focus) or "overview", max_turns=self._limits.max_turns
        )
        return LlmRequest(
            feature=LlmFeature.RESEARCH,
            system=SYSTEM,
            instructions=instructions,
            attribution=LlmAttribution(user_id=user_id, research_run_id=run.id),
            data=(DataBlock("target", f"{target.name} (lane {target.lane})"),),
        )

    async def _finish(
        self,
        user_id: uuid.UUID,
        run_id: uuid.UUID,
        status: str,
        summary: str | None,
        sources: tuple[str, ...],
        progress: _Progress,
    ) -> None:
        values: dict[str, object] = {
            "status": status,
            "summary": summary,
            "sources": list(sources),
            "cost_usd": round(progress.spent_usd, 4),
            "finished_at": self._clock.now(),
        }
        await self._store.update_run(user_id, run_id, values)


class _StepWriter:
    """Numbers, redacts and persists steps. A step the filter can't clear shows its kind only."""

    def __init__(
        self,
        store: ResearchStore,
        redaction: Redaction,
        clock: Clock,
        ids: Ids,
        user_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        self._store = store
        self._redaction = redaction
        self._clock = clock
        self._ids = ids
        self._user_id = user_id
        self._run_id = run_id
        self._seq = 0

    async def write(self, kind: str, text: str, *, tool: str | None = None) -> None:
        self._seq += 1
        summary = await self._redaction.redact_step(text[:4000])
        step = StepRecord(
            id=self._ids.new_id(),
            run_id=self._run_id,
            seq=self._seq,
            kind=kind,
            agent=AGENT,
            tool=tool,
            summary=summary if summary is not None else f"[{kind}]",
            at=self._clock.now(),
        )
        await self._store.add_step(self._user_id, step)
