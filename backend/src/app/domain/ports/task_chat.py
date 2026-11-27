"""Task chat ports: the per-account conversation store, and the agent that answers. Postgres
adapter `app.adapters.db.task_chat`, fake `app.adapters.fakes.task_chat`, agent
`app.agents.task_chat`."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.domain.devboard.model import Actor, PlanRecord, StepKind, TaskRecord, TriageRecord
from app.domain.devboard.proposals import EditChanges, ProposalRecord, ProposalStatus, ProposedEdit

type Author = Literal["user", "assistant"]


@dataclass(frozen=True)
class TaskChatRecord:
    id: uuid.UUID
    task_id: uuid.UUID
    author: Author
    content: str
    created_at: datetime
    proposal_id: uuid.UUID | None = None


class TaskChatStore(Protocol):
    """Every call runs under row-level security for `user_id`: one account never sees another's
    conversation or proposals about the same shared card."""

    async def list_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, offset: int, limit: int
    ) -> list[TaskChatRecord]:
        """Oldest first."""
        ...

    async def recent_messages(
        self, user_id: uuid.UUID, task_id: uuid.UUID, limit: int
    ) -> list[TaskChatRecord]:
        """The last `limit` messages, oldest first."""
        ...

    async def add_message(self, user_id: uuid.UUID, message: TaskChatRecord) -> None: ...

    async def clear(self, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Deletes the conversation and its proposals."""
        ...

    async def add_proposal(self, user_id: uuid.UUID, proposal: ProposalRecord) -> None: ...

    async def get_proposal(
        self, user_id: uuid.UUID, task_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> ProposalRecord | None: ...

    async def proposals(
        self, user_id: uuid.UUID, task_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ProposalRecord]: ...

    async def decide(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, status: ProposalStatus, at: datetime
    ) -> ProposalRecord | None:
        """Pending -> `status`, atomically. None when it was no longer pending."""
        ...


@dataclass(frozen=True)
class ChatContext:
    """What the agent gets: the card, its triage and plan, the conversation so far and the new
    message. Never other cards, the repo or runs."""

    actor: Actor
    task: TaskRecord
    triage: TriageRecord | None
    plan: PlanRecord | None
    history: tuple[TaskChatRecord, ...]
    message: str


class TaskChatTools(Protocol):
    """The agent's tools, bound to one card and one account by the domain service."""

    async def task(self) -> TaskRecord: ...

    async def triage(self) -> TriageRecord | None: ...

    async def plan(self) -> PlanRecord | None: ...

    async def propose(self, edit: ProposedEdit) -> EditChanges:
        """Stores a pending proposal; never edits the card. Raises `ValidationFailed` with a
        detail the model can act on."""
        ...


@dataclass(frozen=True)
class ChatStepNote:
    """One tool call, for the `step` stream. Redacted by the service before release."""

    kind: StepKind
    summary: str
    tool: str | None = None


@dataclass(frozen=True)
class ChatAnswer:
    text: str


class TaskChatAgent(Protocol):
    def reply(
        self, context: ChatContext, tools: TaskChatTools
    ) -> AsyncIterator[ChatStepNote | ChatAnswer]:
        """Step notes while it works, then exactly one answer. Raises a `DomainError` (guard,
        budget, validation, upstream) when it can't answer."""
        ...
