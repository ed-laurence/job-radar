"""Domain wiring for job-search unit tests: in-memory stores, a scripted LLM, real redaction."""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import cast

from pydantic import BaseModel, ValidationError

from app.adapters.fakes.jobs import (
    MemoryChats,
    MemoryFollowups,
    MemoryProfiles,
    MemoryResearch,
    MemoryRoles,
    MemoryRows,
)
from app.adapters.fakes.jobs_dashboard import MemoryDashboard
from app.adapters.fakes.security import FakeContentGuard, FakePII
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.agents.research.agent import ResearchAgent
from app.agents.research.service import ResearchService
from app.agents.research.tools import KnownDataLookup
from app.compose.jobs import GuardRedaction
from app.domain.errors import GuardBlocked, ValidationFailed
from app.domain.identity import AppRole, Principal
from app.domain.jobs.chat import ChatService
from app.domain.jobs.crm import ContactService, TargetService
from app.domain.jobs.dashboard import DashboardService
from app.domain.jobs.followups import FollowupService
from app.domain.jobs.kits import KitService
from app.domain.jobs.records import ContactRecord, TargetRecord
from app.domain.jobs.rejections import RejectionService
from app.domain.jobs.roles import RoleService
from app.domain.jobs.scoring import ScoringService
from app.domain.ports import (
    DataBlock,
    FinalAnswer,
    LlmFeature,
    LlmRequest,
    LlmResult,
    ToolCall,
    ToolOutput,
    ToolTurn,
)
from app.guards.redaction import Redactor

ALICE = Principal(user_id=uuid.UUID(int=1), email="alice@example.test", role=AppRole.MAINTAINER)
BOB = Principal(user_id=uuid.UUID(int=2), email="bob@example.test", role=AppRole.USER)


class ScriptedLlm:
    """The LLM port with canned answers per feature, in order. Records every request."""

    def __init__(self, guard: FakeContentGuard) -> None:
        self._guard = guard
        self.replies: dict[LlmFeature, list[str]] = defaultdict(list)
        self.requests: list[LlmRequest] = []

    def script(self, feature: LlmFeature, *texts: str) -> None:
        self.replies[feature].extend(texts)

    async def generate[T: BaseModel](self, request: LlmRequest, output: type[T]) -> LlmResult[T]:
        self.requests.append(request)
        await self._check(request)
        text = self.replies[request.feature].pop(0)
        try:
            value = output.model_validate_json(text)
        except ValidationError:
            # The real adapter retries, falls back, then raises this typed error.
            raise ValidationFailed("The model's answer could not be used.") from None
        return LlmResult(value, "replay", "scripted", is_fallback=False)

    async def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        await self._check(request)
        text = self.replies[request.feature].pop(0)
        for start in range(0, len(text), 7):
            yield text[start : start + 7]

    async def next_action[T: BaseModel](
        self, request: LlmRequest, turn: ToolTurn, answer: type[T]
    ) -> ToolCall | FinalAnswer[T]:
        """Scripted in the research decision shape. `{"action": "tool", "tool", "query",
        "thought"}` calls `tool` with `{"topic": query}`; `{"action": "finish", ...}` answers
        with the summary, findings and sources. Tool outputs so far are recorded (and guarded)
        as `tool_result_<n>` data blocks, the way the real adapter treats them as data."""
        outputs = [item for item in turn.transcript if isinstance(item, ToolOutput)]
        blocks = [DataBlock(f"tool_result_{n}", out.content) for n, out in enumerate(outputs, 1)]
        seen = dataclasses.replace(request, data=(*request.data, *blocks))
        self.requests.append(seen)
        await self._check(seen)
        decision = cast("dict[str, object]", json.loads(self.replies[request.feature].pop(0)))
        if decision.get("action") == "tool":
            return ToolCall(
                id=f"call-{len(outputs) + 1}",
                name=str(decision.get("tool") or ""),
                arguments={"topic": str(decision.get("query") or "")},
                text=str(decision.get("thought") or ""),
            )
        fields = {
            key: decision[key] for key in ("summary", "findings", "sources") if key in decision
        }
        try:
            return FinalAnswer(answer.model_validate(fields))
        except ValidationError:
            raise ValidationFailed("The model's answer could not be used.") from None

    async def _check(self, request: LlmRequest) -> None:
        """Like the real adapter: untrusted data blocks pass the input guard, fail closed."""
        for block in request.data:
            if (await self._guard.inspect_input(block.text)).is_blocked:
                raise GuardBlocked("This content can't be sent to the model.")


@dataclass
class Domain:
    clock: FakeClock = field(default_factory=FakeClock)
    ids: SequentialIds = field(default_factory=SequentialIds)
    guard: FakeContentGuard = field(default_factory=FakeContentGuard)
    roles_store: MemoryRoles = field(default_factory=MemoryRoles)
    contacts_store: MemoryRows[ContactRecord] = field(
        default_factory=lambda: MemoryRows(ContactRecord, "url")
    )
    targets_store: MemoryRows[TargetRecord] = field(
        default_factory=lambda: MemoryRows(TargetRecord, "name")
    )
    chats_store: MemoryChats = field(default_factory=MemoryChats)
    research_store: MemoryResearch = field(default_factory=MemoryResearch)
    profiles: MemoryProfiles = field(default_factory=MemoryProfiles)
    followups_store: MemoryFollowups = field(default_factory=MemoryFollowups)

    def __post_init__(self) -> None:
        self.llm = ScriptedLlm(self.guard)
        redaction = GuardRedaction(Redactor(("Project Bluebird",), FakePII(), self.guard))
        self.roles = RoleService(self.roles_store, self.guard, self.clock, self.ids)
        self.contacts = ContactService(self.contacts_store, self.clock, self.ids)
        self.targets = TargetService(self.targets_store, self.clock, self.ids)
        self.scoring = ScoringService(
            self.roles_store, self.roles, self.profiles, self.llm, self.clock, self.ids
        )
        self.kits = KitService(
            self.roles, self.roles_store, self.profiles, self.llm, redaction, self.clock
        )
        self.chat = ChatService(
            self.roles, self.chats_store, self.profiles, self.llm, redaction, self.clock, self.ids
        )
        self.rejections = RejectionService(self.roles, self.roles_store, self.llm, self.clock)
        self.followups = FollowupService(
            self.roles, self.contacts_store, self.followups_store, self.llm, self.ids
        )
        tools = [KnownDataLookup(self.roles_store, self.contacts_store)]
        agent = ResearchAgent(self.llm, self.research_store, tools, redaction, self.clock, self.ids)
        self.research = ResearchService(
            self.targets, self.research_store, agent, self.clock, self.ids
        )
        self.dashboard = DashboardService(MemoryDashboard(self.roles_store), self.clock)
