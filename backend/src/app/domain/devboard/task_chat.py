"""Task chat (spec tag "Task chat"): discuss one dev-board card with Claude, which may propose an
edit that the person accepts or rejects (`task_chat_decisions`).

- Same rights as editing the card (`updateTask`): any signed-in account. Each account has its
  own conversation and proposals (row-level security on both tables).
- The person's message passes the content guard before anything is saved (fail closed). The
  card's text reaches the model only as delimited data; the reply is released through the
  segment filter, and every tool step through the step redactor (ARCH-30).
- A proposal is stored, never applied, by the agent's `propose_task_edit` tool.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.domain.devboard.board import BoardService, decode_cursor, encode_cursor
from app.domain.devboard.model import Actor, Page, StepRecord
from app.domain.devboard.plans import PlanService
from app.domain.devboard.proposals import ProposalRecord
from app.domain.devboard.task_chat_tools import BoundTools
from app.domain.errors import Conflict, DomainError, GuardBlocked
from app.domain.jobs.ports import Redaction
from app.domain.jobs.streams import Done, Failed, Retract, Segment, SegmentRelay
from app.domain.ports import PII, Clock, ContentGuard, Ids
from app.domain.ports.task_chat import (
    ChatAnswer,
    ChatContext,
    ChatStepNote,
    TaskChatAgent,
    TaskChatRecord,
    TaskChatStore,
)

MAX_HISTORY = 40
MAX_REPLY_CHARS = 16000
AGENT = "task-chat"


@dataclass(frozen=True)
class ChatStep:
    """A tool step on the reply stream. `step.run_id` is the reply's message id."""

    seq: int
    step: StepRecord


type TaskChatEvent = Segment | Retract | ChatStep | Done | Failed


@dataclass(frozen=True)
class ChatMessageView:
    message: TaskChatRecord
    proposal: ProposalRecord | None = None


class TaskChatService:
    def __init__(
        self,
        services: tuple[BoardService, PlanService],
        store: TaskChatStore,
        agent: TaskChatAgent,
        guards: tuple[ContentGuard, PII, Redaction],
        system: tuple[Clock, Ids],
    ) -> None:
        self._board, self._plans = services
        self._store = store
        self._agent = agent
        self._guard, self._pii, self._redaction = guards
        self._system = system
        self._clock, self._ids = system
        self._busy: set[tuple[uuid.UUID, uuid.UUID]] = set()

    # ------------------------------------------------------------ history
    async def list_messages(
        self, actor: Actor, task_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[ChatMessageView]:
        await self._board.get(actor, task_id)
        offset = decode_cursor(cursor)
        rows = await self._store.list_messages(actor.user_id, task_id, offset, limit + 1)
        page, more = rows[:limit], len(rows) > limit
        wanted = [row.proposal_id for row in page if row.proposal_id is not None]
        found = await self._store.proposals(actor.user_id, task_id, wanted) if wanted else []
        by_id = {proposal.id: proposal for proposal in found}
        views = [
            ChatMessageView(row, by_id.get(row.proposal_id) if row.proposal_id else None)
            for row in page
        ]
        return Page(items=views, next_cursor=encode_cursor(offset + limit) if more else None)

    async def clear(self, actor: Actor, task_id: uuid.UUID) -> None:
        await self._board.get(actor, task_id)
        await self._store.clear(actor.user_id, task_id)

    # ------------------------------------------------------------ chat
    async def send(
        self, actor: Actor, task_id: uuid.UUID, content: str
    ) -> AsyncIterator[TaskChatEvent]:
        """Guard and save the message, then return the reply stream. Raises before streaming."""
        task = await self._board.get(actor, task_id)
        if (await self._guard.inspect_input(content)).is_blocked:
            raise GuardBlocked("Your message was blocked by the safety filter.")
        key = (actor.user_id, task_id)
        if key in self._busy:
            raise Conflict("A reply about this card is still being written.")
        history = await self._store.recent_messages(actor.user_id, task_id, MAX_HISTORY)
        message = TaskChatRecord(
            id=self._ids.new_id(),
            task_id=task_id,
            author="user",
            content=content,
            created_at=self._clock.now(),
        )
        await self._store.add_message(actor.user_id, message)
        self._busy.add(key)
        context = ChatContext(
            actor=actor,
            task=task,
            triage=await self._board.latest_triage(actor, task_id),
            plan=await self._plans.latest(actor, task_id),
            history=tuple(history),
            message=content,
        )
        return self._reply(context)

    async def _reply(self, context: ChatContext) -> AsyncIterator[TaskChatEvent]:
        actor, task_id = context.actor, context.task.id
        relay = SegmentRelay(self._redaction)
        reply_id = self._ids.new_id()
        services = (self._board, self._plans)
        tools = BoundTools(services, self._store, self._pii, self._system, (actor, task_id))
        try:
            answer = ""
            async for item in self._agent.reply(context, tools):
                if isinstance(item, ChatAnswer):
                    answer = item.text
                else:
                    step = await self._step(reply_id, relay.next_seq(), item)
                    yield ChatStep(seq=step.seq, step=step)
            async for event in relay.relay(_once(answer), "chat"):
                yield event
            reply = TaskChatRecord(
                id=reply_id,
                task_id=task_id,
                author="assistant",
                content=relay.texts.get("chat", "")[:MAX_REPLY_CHARS],
                created_at=self._clock.now(),
                proposal_id=tools.proposal.id if tools.proposal else None,
            )
            await self._store.add_message(actor.user_id, reply)
            yield Done(seq=relay.next_seq(), result_id=reply.id)
        except DomainError as error:
            yield Failed(seq=relay.next_seq(), error=error)
        finally:
            self._busy.discard((actor.user_id, task_id))

    async def _step(self, reply_id: uuid.UUID, seq: int, note: ChatStepNote) -> StepRecord:
        summary = await self._redaction.redact_step(note.summary[:4000])
        return StepRecord(
            id=self._ids.new_id(),
            run_id=reply_id,
            seq=seq,
            kind=note.kind,
            summary=summary if summary is not None else f"[{note.kind.value}]",
            at=self._clock.now(),
            agent=AGENT,
            tool=note.tool,
        )


async def _once(text: str) -> AsyncIterator[str]:
    yield text
