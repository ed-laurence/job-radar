"""The task-chat agent: a manual tool-use loop through the LLM port (ARCH-20), like research.

Each turn the model calls one of the card's tools or gives its answer, validated by
`TaskChatAnswer` (ARCH-21). The agent runs the tool through the domain's bound tools, reports a
step note for the stream, and adds the call and its result to the transcript; the adapter guards
every tool result before the next turn and the turn's text on the way out (SEC-04).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.llm.providers.replay import ReplayFixtureMissing
from app.agents.task_chat import tools as chat_tools
from app.agents.task_chat.prompt import INSTRUCTIONS, SYSTEM
from app.domain.devboard.model import StepKind
from app.domain.devboard.proposals import ProposedEdit, describe
from app.domain.errors import UpstreamUnavailable, ValidationFailed
from app.domain.ports import (
    LLM,
    DataBlock,
    FinalAnswer,
    LlmAttribution,
    LlmFeature,
    LlmRequest,
    ToolCall,
    ToolOutput,
    ToolTurn,
    TranscriptItem,
)
from app.domain.ports.task_chat import (
    ChatAnswer,
    ChatContext,
    ChatStepNote,
    TaskChatRecord,
    TaskChatTools,
)

MAX_TURNS = 5
OUT_OF_TURNS = "I ran out of steps before finishing this answer. Ask again, a little more narrowly."


class TaskChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=8000)


class TaskChatLlmAgent:
    def __init__(self, llm: LLM, *, max_turns: int = MAX_TURNS) -> None:
        self._llm = llm
        self._max_turns = max_turns

    async def reply(
        self, context: ChatContext, tools: TaskChatTools
    ) -> AsyncIterator[ChatStepNote | ChatAnswer]:
        request = _request(context)
        transcript: list[TranscriptItem] = []
        for _ in range(self._max_turns):
            action = await self._next(request, transcript)
            if isinstance(action, FinalAnswer):
                yield ChatAnswer(action.value.reply)
                return
            output, note = await _run(action, tools)
            yield note
            transcript += [action, output]
        yield ChatAnswer(OUT_OF_TURNS)

    async def _next(
        self, request: LlmRequest, transcript: Sequence[TranscriptItem]
    ) -> ToolCall | FinalAnswer[TaskChatAnswer]:
        turn = ToolTurn(tools=chat_tools.TOOLS, transcript=tuple(transcript))
        try:
            return await self._llm.next_action(request, turn, TaskChatAnswer)
        except ReplayFixtureMissing:
            # Offline mode has recordings only for known conversations; say so, don't crash.
            raise UpstreamUnavailable("No offline recording for this conversation.") from None


async def _run(call: ToolCall, tools: TaskChatTools) -> tuple[ToolOutput, ChatStepNote]:
    if call.name == chat_tools.GET_TASK:
        text = chat_tools.task_text(await tools.task())
    elif call.name == chat_tools.GET_TRIAGE:
        text = chat_tools.triage_text(await tools.triage())
    elif call.name == chat_tools.GET_PLAN:
        text = chat_tools.plan_text(await tools.plan())
    elif call.name == chat_tools.PROPOSE:
        return await _propose(call, tools)
    else:
        note = ChatStepNote(StepKind.TOOL_CALL, "Asked for a tool that doesn't exist.", call.name)
        unknown = f"There is no tool called {call.name[:64]!r}."
        return ToolOutput(call.id, unknown, is_error=True), note
    summary = chat_tools.STEP_SUMMARIES[call.name]
    return ToolOutput(call.id, text), ChatStepNote(StepKind.TOOL_CALL, summary, call.name)


async def _propose(call: ToolCall, tools: TaskChatTools) -> tuple[ToolOutput, ChatStepNote]:
    try:
        parsed = chat_tools.ProposeInput.model_validate(dict(call.arguments))
        criteria = parsed.acceptance_criteria
        changes = await tools.propose(
            ProposedEdit(
                reason=parsed.reason,
                title=parsed.title,
                description=parsed.description,
                acceptance_criteria=None if criteria is None else tuple(criteria),
            )
        )
    except ValidationError:
        detail = "Invalid input: pass title, description, acceptance_criteria and reason."
        return _refused(call, detail)
    except ValidationFailed as error:
        return _refused(call, error.detail or "The proposal was refused.")
    note = ChatStepNote(StepKind.TOOL_CALL, describe(changes), call.name)
    return ToolOutput(call.id, chat_tools.proposed_text(changes)), note


def _refused(call: ToolCall, detail: str) -> tuple[ToolOutput, ChatStepNote]:
    note = ChatStepNote(StepKind.TOOL_RESULT, f"Couldn't propose the edit: {detail}", call.name)
    return ToolOutput(call.id, f"Not saved: {detail}", is_error=True), note


def _request(context: ChatContext) -> LlmRequest:
    task = context.task
    blocks = [
        DataBlock("task", chat_tools.task_text(task)),
        DataBlock("triage", chat_tools.triage_text(context.triage)),
        DataBlock("plan", chat_tools.plan_summary(context.plan)),
    ]
    if context.history:
        blocks.append(DataBlock("conversation", _conversation(context.history)))
    return LlmRequest(
        feature=LlmFeature.TASK_CHAT,
        system=SYSTEM,
        instructions=INSTRUCTIONS.format(message=context.message),
        attribution=LlmAttribution(user_id=context.actor.user_id),
        data=tuple(blocks),
    )


def _conversation(history: Sequence[TaskChatRecord]) -> str:
    turns = [{"author": item.author, "content": item.content} for item in history]
    return json.dumps(turns, ensure_ascii=False)
