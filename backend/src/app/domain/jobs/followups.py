"""Follow-up drafts after applying (ported from job-seeker's `followups.py`), on the open-model
route (`followup` in `ai/models.yaml`) with the Claude fallback.

Code enforces what the model can get wrong: names must appear in the ad, contacts must be the
user's own, emails must be in the ad, and the message mustn't sell. `FollowupStream` streams the
drafts through the segment filter for the SSE endpoint.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import DomainError, ValidationFailed
from app.domain.identity import Principal
from app.domain.jobs.crm import company_key
from app.domain.jobs.ports import ContactStore, FollowupStore, Redaction
from app.domain.jobs.prompts import listing_block
from app.domain.jobs.records import ContactRecord, FollowupRecord, RoleRecord
from app.domain.jobs.roles import RoleService
from app.domain.jobs.streams import Done, Failed, SegmentRelay, StreamEvent
from app.domain.ports import LLM, DataBlock, Ids, LlmAttribution, LlmFeature, LlmRequest

MAX_TARGETS = 3
FOLLOWUP_PART = "followup"
_APPLIED = frozenset({"applied", "interview", "offer"})
_PITCH = re.compile(
    r"\b(?:I|I've|I have|I'd)\s+(?:also\s+)?(?:led|built|delivered|cut|grew|scaled|drove|designed|"
    r"architected|spent|managed|reduced|increased|launched|founded|implemented|shipped|ran|headed)\b",
    re.IGNORECASE,
)
_METRIC = re.compile(
    r"[~<>]?\d+(?:[.,]\d+)?\s*(?:\+|%|x\b|k\b|m\b|engineers|years|yrs|people)", re.IGNORECASE
)
_PLACEHOLDER = re.compile(r"\[(?!first name\])[^\]]*\]|\{[^}]*\}", re.IGNORECASE)
_TELLS = ("hope this finds you", "reach out", "touch base", "excited", "passionate", "amazing")

FOLLOWUP_SYSTEM = (
    "A job seeker applied for a role and wants to follow up a day or two later. Pick who to "
    "contact and draft a short message for each. They send the messages themselves."
)
FOLLOWUP_INSTRUCTIONS = """Who (1-3 targets, best first):
1. Someone named in the ad (source "ad"), using the name exactly as written. Never invent a name.
2. One of the candidate's contacts at the company (source "connection", with its contact_id).
3. Otherwise a people search (source "search", name null) with search keywords such as
   "talent acquisition" or the likely hiring manager title.
Channel: "email" only if the ad gives that person's address (copy it exactly); "linkedin_message"
for contacts; "connection_request" for anyone else.
Message: genuine interest, not a pitch. No achievements, numbers, technologies or employers.
Open "Hi {first name}," (or "Hi [first name]," for a search target), say one specific true thing
about the role, say plainly that they've applied, and end with one easy question.
connection_request: at most 280 characters. linkedin_message and email: 40-90 words.
why: one short line on why this person."""


class FollowupTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    role: str = Field(default="", max_length=200)
    source: Literal["ad", "connection", "search"]
    contact_id: str | None = None
    channel: Literal["linkedin_message", "connection_request", "email"]
    email: str | None = Field(default=None, max_length=254)
    why: str = Field(max_length=300)
    message: str = Field(max_length=2000)


class FollowupPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[FollowupTarget] = Field(min_length=1, max_length=5)


def message_problems(target: FollowupTarget) -> list[str]:
    problems: list[str] = []
    message = target.message.strip()
    selling = sorted({m.group(0) for m in _PITCH.finditer(message)})
    selling += sorted({m.group(0).strip() for m in _METRIC.finditer(message)})
    if selling:
        problems.append("it's selling (" + ", ".join(repr(s) for s in selling) + ")")
    tells = sorted({tell for tell in _TELLS if tell in message.lower()})
    if tells:
        problems.append("remove: " + ", ".join(repr(tell) for tell in tells))
    is_search = target.source == "search"
    if _PLACEHOLDER.search(message) or ("[first name]" in message.lower() and not is_search):
        problems.append("no placeholders (only [first name] for a search target)")
    if target.channel == "connection_request" and len(message) > 300:
        problems.append(f"connection request is {len(message)} characters; the limit is 300")
    if target.channel in ("linkedin_message", "email"):
        words = len(re.findall(r"\b[\w'-]+\b", message))
        if not 30 <= words <= 110:
            problems.append(f"{words} words; keep it to 40-90")
    if message.count("!") > 1:
        problems.append("at most one exclamation mark")
    return problems


def checked_target(
    target: FollowupTarget, ad_text: str, contacts: dict[str, ContactRecord]
) -> FollowupTarget | None:
    """Enforce real names, real contacts and real emails. `None` drops the target."""
    ad_lower = ad_text.lower()
    updated = target
    if target.source == "connection":
        contact = contacts.get(target.contact_id or "")
        if contact is None:
            return None
        name = " ".join(p for p in (contact.first_name, contact.last_name) if p) or None
        updated = target.model_copy(
            update={"name": name, "role": target.role or contact.position or ""}
        )
    elif target.source == "ad" and (not target.name or target.name.lower() not in ad_lower):
        updated = target.model_copy(update={"source": "search", "name": None})
    if updated.email and updated.email.lower() not in ad_lower:
        channel = "connection_request" if updated.channel == "email" else updated.channel
        updated = updated.model_copy(update={"email": None, "channel": channel})
    if updated.source == "search" and updated.channel == "linkedin_message":
        updated = updated.model_copy(update={"channel": "connection_request", "name": None})
    return updated


class FollowupService:
    def __init__(
        self,
        role_service: RoleService,
        contacts: ContactStore,
        followups: FollowupStore,
        llm: LLM,
        ids: Ids,
    ) -> None:
        self._role_service = role_service
        self._contacts = contacts
        self._followups = followups
        self._llm = llm
        self._ids = ids

    async def applied_role(self, principal: Principal, role_id: uuid.UUID) -> RoleRecord:
        role = await self._role_service.get_role(principal, role_id)
        if role.status not in _APPLIED:
            raise ValidationFailed("Follow-ups are for roles you've applied for.")
        return role

    async def draft(self, principal: Principal, role_id: uuid.UUID) -> list[FollowupRecord]:
        role = await self.applied_role(principal, role_id)
        contacts = await self._contacts_at(principal, role)
        plan = await self._plan(principal, role, contacts)
        rows: list[dict[str, object]] = []
        for target in plan.targets[:MAX_TARGETS]:
            checked = checked_target(target, role.description or "", contacts)
            if checked is not None:
                rows.append(self._row(checked, len(rows), contacts))
        return await self._followups.replace_suggestions(principal.user_id, role_id, rows)

    async def _contacts_at(
        self, principal: Principal, role: RoleRecord
    ) -> dict[str, ContactRecord]:
        key = company_key(role.company)
        if key is None:
            return {}
        rows = await self._contacts.list_rows(principal.user_id, {"company_norm": key}, 0, 20)
        return {str(contact.id): contact for contact in rows}

    async def _plan(
        self, principal: Principal, role: RoleRecord, contacts: dict[str, ContactRecord]
    ) -> FollowupPlan:
        people = "\n".join(
            f"- contact_id {cid}: {c.first_name or ''} {c.last_name or ''}, {c.position or '?'}"
            for cid, c in contacts.items()
        )
        blocks = [listing_block(role), DataBlock("job_ad", (role.description or "")[:20_000])]
        if people:
            blocks.append(DataBlock("contacts_at_company", people))
        request = LlmRequest(
            feature=LlmFeature.FOLLOWUP,
            system=FOLLOWUP_SYSTEM,
            instructions=FOLLOWUP_INSTRUCTIONS,
            attribution=LlmAttribution(user_id=principal.user_id, role_id=role.id),
            data=tuple(block for block in blocks if block.text),
        )
        return (await self._llm.generate(request, FollowupPlan)).value

    def _row(
        self, target: FollowupTarget, priority: int, contacts: dict[str, ContactRecord]
    ) -> dict[str, object]:
        contact = contacts.get(target.contact_id or "") if target.source == "connection" else None
        return {
            "id": self._ids.new_id(),
            "priority": priority,
            "status": "suggested",
            "target_name": target.name,
            "target_role": target.role or None,
            "source": target.source,
            "contact_id": contact.id if contact else None,
            "channel": target.channel,
            "email": target.email,
            "why": target.why.strip(),
            "message": target.message.strip(),
            "problems": message_problems(target),
        }


class FollowupStream:
    """Follow-up drafts as a typed event stream (ARCH-30): every draft passes the segment
    filter (`Redaction` port) before release, one relay per target."""

    def __init__(self, followups: FollowupService, redaction: Redaction) -> None:
        self._followups = followups
        self._redaction = redaction

    async def open_draft(
        self, principal: Principal, role_id: uuid.UUID
    ) -> AsyncIterator[StreamEvent]:
        """Check the role before the stream starts, so errors are plain problem responses."""
        await self._followups.applied_role(principal, role_id)
        return self._run(principal, role_id)

    async def _run(self, principal: Principal, role_id: uuid.UUID) -> AsyncIterator[StreamEvent]:
        relay = SegmentRelay(self._redaction)
        try:
            drafts = await self._followups.draft(principal, role_id)
            for draft in drafts:
                key = f"{FOLLOWUP_PART}{draft.priority}"
                async for event in relay.relay(_once(draft_text(draft)), FOLLOWUP_PART, key=key):
                    yield event
            first = drafts[0].id if drafts else None
            yield Done(seq=relay.next_seq(), result_id=first, status=f"{len(drafts)} drafts")
        except DomainError as error:
            yield Failed(seq=relay.next_seq(), error=error)


def draft_text(draft: FollowupRecord) -> str:
    who = draft.target_name or "Someone at the company"
    role = f" ({draft.target_role})" if draft.target_role else ""
    channel = (draft.channel or "message").replace("_", " ")
    lines = [f"To: {who}{role}, by {channel}.", draft.why or "", draft.message or ""]
    return "\n\n".join(line for line in lines if line) + "\n\n"


async def _once(text: str) -> AsyncIterator[str]:
    yield text
