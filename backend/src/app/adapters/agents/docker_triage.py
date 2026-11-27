"""DockerTriageAgent: the `TriageAgent` port over the read-only sandboxed triager.

Code, not the model, checks the card first: the content guard (fail closed) and the PII
service (de-identification must work; the coordinator's redaction filter masks what the agent
reads through get_task). Only then is the sandbox launched (`DockerAgentRunner`, triage mode),
which can read and search the repo and run existing tests to reproduce. The step messages and
the `GuardBlocked` text match the in-app triager's (`app.agents.triage.agent`), so the board
reads the same whichever one ran. A run that ends without a valid `/out/triage.json` raises
`UpstreamUnavailable` with the runner's safe reason (it points at the kept run logs).
"""

from __future__ import annotations

from dataclasses import replace

from app.adapters.agents.docker_runner import DockerAgentRunner, SandboxResult
from app.domain.devboard.model import NewStep, StepKind
from app.domain.errors import GuardBlocked, UpstreamUnavailable
from app.domain.ports import PII, ContentGuard
from app.domain.ports.devboard import RunContext, RunTools, TriageDecision

AGENT = "triage"
TOOL = "sandbox.triage"


def task_text(title: str, body: str) -> str:
    """The text the guard inspects (the same shape as the in-app triager's)."""
    return f"Title: {title}\n\n{body}".strip()


class DockerTriageAgent:
    def __init__(self, runner: DockerAgentRunner, guard: ContentGuard, pii: PII) -> None:
        self._runner = runner
        self._guard = guard
        self._pii = pii

    def __repr__(self) -> str:
        return f"DockerTriageAgent({self._runner!r})"

    async def triage(self, context: RunContext, tools: RunTools) -> TriageDecision:
        await tools.emit(NewStep(StepKind.STATUS, "Triage started.", AGENT))
        text = task_text(context.task.title, context.task.body)
        verdict = await self._guard.inspect_input(text)
        if verdict.is_blocked:
            await tools.emit(NewStep(StepKind.STATUS, "The safety filter blocked the text.", AGENT))
            raise GuardBlocked("The card's text was blocked by the safety filter.")
        await self._pii.deidentify(text)
        await tools.emit(
            NewStep(StepKind.THOUGHT, "Text passed the safety filter; personal data masked.", AGENT)
        )
        await tools.emit(
            NewStep(StepKind.TOOL_CALL, "Read-only sandboxed triage run.", AGENT, tool=TOOL)
        )
        result = await self._runner.run(context, tools)
        decision = result.triage if isinstance(result, SandboxResult) else None
        if not result.is_success or decision is None:
            raise UpstreamUnavailable(result.outcome)
        await tools.emit(NewStep(StepKind.TOOL_RESULT, result.outcome, AGENT, tool=TOOL))
        return replace(decision, cost_usd=result.cost_usd, model=result.model or decision.model)
