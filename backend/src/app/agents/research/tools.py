"""Tools the research agent may call. Results are untrusted data for the next model turn.

The default tool, `lookup_known_data`, answers from the caller's own records (the target, roles
at the company, contacts there), so the agent runs offline and under `LLM=replay`. Web search
is not a tool here: when `RESEARCH_WEB_SEARCH` is on, composition offers the provider's own
server tool instead (off by default).

Each tool describes its input with a Pydantic model (`extra="forbid"`), which is both the strict
JSON schema the model sees and the validation of the arguments it sends (ARCH-21).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.jobs.crm import company_key
from app.domain.jobs.ports import ContactStore, RoleQuery, RoleStore
from app.domain.jobs.records import TargetRecord
from app.domain.ports import ToolSpec

MAX_RESULT_CHARS = 4000
TOPICS = ("target", "roles", "contacts")


@dataclass(frozen=True)
class ToolResult:
    text: str
    sources: tuple[str, ...] = ()
    is_error: bool = False


class ResearchTool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def run(
        self, user_id: uuid.UUID, target: TargetRecord, arguments: Mapping[str, object]
    ) -> ToolResult:
        """Invalid arguments come back as an error result for the model, never an exception."""
        ...


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal["target", "roles", "contacts"] = Field(
        description="target: the user's notes on the company; roles: roles they've tracked "
        "there; contacts: people they know there."
    )


class KnownDataLookup:
    name = "lookup_known_data"
    description = (
        "Look up what the user already has on file about the company. Call this before "
        "drawing conclusions: first `target` for their notes, then `roles` and `contacts`."
    )

    def __init__(self, roles: RoleStore, contacts: ContactStore) -> None:
        self._roles = roles
        self._contacts = contacts

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, LookupInput.model_json_schema())

    async def run(
        self, user_id: uuid.UUID, target: TargetRecord, arguments: Mapping[str, object]
    ) -> ToolResult:
        try:
            parsed = LookupInput.model_validate(dict(arguments))
        except ValidationError:
            return ToolResult(
                f"Invalid input. `topic` is one of: {', '.join(TOPICS)}.", is_error=True
            )
        return await self.lookup(user_id, target, parsed.topic)

    async def lookup(self, user_id: uuid.UUID, target: TargetRecord, topic: str) -> ToolResult:
        if topic == "roles":
            return await self._roles_at(user_id, target)
        if topic == "contacts":
            return await self._contacts_at(user_id, target)
        if topic == "target":
            return _target_notes(target)
        return ToolResult(f"Unknown topic {topic[:40]!r}. Use one of: {', '.join(TOPICS)}.")

    async def _roles_at(self, user_id: uuid.UUID, target: TargetRecord) -> ToolResult:
        query = RoleQuery(text=target.name[:100], sort="-found_at")
        roles = await self._roles.list_roles(user_id, query, 0, 20)
        if not roles:
            return ToolResult("No tracked roles at this company.")
        lines = [
            f"- {role.title} ({role.status}, score {_score(role.score)}"
            f", found {role.found_at.date().isoformat()})"
            for role in roles
        ]
        sources = tuple(role.url for role in roles if role.url)
        return ToolResult(_clip("\n".join(lines)), sources[:10])

    async def _contacts_at(self, user_id: uuid.UUID, target: TargetRecord) -> ToolResult:
        key = company_key(target.name)
        contacts = await self._contacts.list_rows(user_id, {"company_norm": key}, 0, 20)
        if not contacts:
            return ToolResult("No known contacts at this company.")
        lines = [
            f"- {c.first_name or ''} {c.last_name or ''}: {c.position or 'position unknown'}"
            f" (status {c.status})"
            for c in contacts
        ]
        return ToolResult(_clip("\n".join(lines)))


def _target_notes(target: TargetRecord) -> ToolResult:
    parts = [
        f"Name: {target.name}",
        f"Lane: {target.lane}",
        f"Status: {target.status}",
        f"Why it fits: {target.why}" if target.why else "",
        f"Who to approach: {target.approach}" if target.approach else "",
        f"Caveat: {target.caveat}" if target.caveat else "",
        f"Notes: {target.notes}" if target.notes else "",
    ]
    return ToolResult(_clip("\n".join(p for p in parts if p)), tuple(target.sources))


def _clip(text: str) -> str:
    return text if len(text) <= MAX_RESULT_CHARS else text[: MAX_RESULT_CHARS - 1] + "…"


def _score(score: int | None) -> str:
    return "n/a" if score is None else str(score)
