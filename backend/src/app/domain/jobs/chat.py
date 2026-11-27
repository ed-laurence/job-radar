"""Role chat, streamed (ported from job-seeker's `chat.py`, minus files and the persona).

The user's message is their own request, so it goes in the instructions; the ad, the kit and
earlier turns are data blocks. Only the filtered reply is stored.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from app.domain.errors import Conflict, DomainError
from app.domain.identity import Principal
from app.domain.jobs.pagination import decode_cursor, to_page
from app.domain.jobs.ports import ChatStore, ProfileStore, Redaction
from app.domain.jobs.prompts import FACT_RULES, VOICE_RULES, role_blocks
from app.domain.jobs.records import ChatRecord, Page, RoleRecord
from app.domain.jobs.roles import RoleService
from app.domain.jobs.streams import Done, Failed, SegmentRelay, StreamEvent
from app.domain.ports import LLM, Clock, DataBlock, Ids, LlmAttribution, LlmFeature, LlmRequest

MAX_HISTORY = 40

CHAT_SYSTEM = (
    "You are a job-search assistant talking with the candidate about one specific role. "
    "Everything you know about it is in the data blocks: the listing, the ad, the assessment, "
    "the application kit and the conversation so far."
)
CHAT_INSTRUCTIONS = f"""Answer the candidate's message directly and specifically to this role.
Be candid about fit and gaps; don't flatter. When they ask for something to send, write it ready to
use in their voice, short. They send everything themselves; never say you sent anything. You can't
browse the web, so say when you rely on general knowledge.

{FACT_RULES}
{VOICE_RULES}
Use short paragraphs and bullet points where they help."""


class ChatService:
    def __init__(
        self,
        role_service: RoleService,
        chats: ChatStore,
        profiles: ProfileStore,
        llm: LLM,
        redaction: Redaction,
        clock: Clock,
        ids: Ids,
    ) -> None:
        self._role_service = role_service
        self._chats = chats
        self._profiles = profiles
        self._llm = llm
        self._redaction = redaction
        self._clock = clock
        self._ids = ids
        self._busy: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def list_messages(
        self, principal: Principal, role_id: uuid.UUID, cursor: str | None, limit: int
    ) -> Page[ChatRecord]:
        await self._role_service.get_role(principal, role_id)
        offset = decode_cursor(cursor)
        rows = await self._chats.list_messages(principal.user_id, role_id, offset, limit + 1)
        return to_page(rows, offset, limit)

    async def clear(self, principal: Principal, role_id: uuid.UUID) -> None:
        await self._role_service.get_role(principal, role_id)
        await self._chats.clear(principal.user_id, role_id)

    async def send(
        self, principal: Principal, role_id: uuid.UUID, content: str
    ) -> AsyncIterator[StreamEvent]:
        """Save the user's message, then return the reply stream. Raises before streaming."""
        role = await self._role_service.get_role(principal, role_id)
        key = (principal.user_id, role_id)
        if key in self._busy:
            raise Conflict("A reply is still being written for this role.")
        history = await self._chats.recent_messages(principal.user_id, role_id, MAX_HISTORY)
        message = ChatRecord(
            id=self._ids.new_id(),
            role_id=role_id,
            author="user",
            content=content,
            created_at=self._clock.now(),
        )
        await self._chats.add_message(principal.user_id, message)
        self._busy.add(key)
        return self._reply(principal, role, history, content)

    async def _reply(
        self, principal: Principal, role: RoleRecord, history: list[ChatRecord], content: str
    ) -> AsyncIterator[StreamEvent]:
        relay = SegmentRelay(self._redaction)
        try:
            profile = await self._profiles.load_profile(principal.user_id)
            blocks = [*role_blocks(role, profile), _kit_block(role)]
            if history:
                turns = [{"author": m.author, "content": m.content} for m in history]
                blocks.append(DataBlock("conversation", json.dumps(turns, ensure_ascii=False)))
            request = LlmRequest(
                feature=LlmFeature.CHAT,
                system=CHAT_SYSTEM,
                instructions=f"{CHAT_INSTRUCTIONS}\n\nThe candidate's message:\n{content}",
                attribution=LlmAttribution(user_id=principal.user_id, role_id=role.id),
                data=tuple(block for block in blocks if block.text),
            )
            async for event in relay.relay(self._llm.stream(request), "chat"):
                yield event
            reply = ChatRecord(
                id=self._ids.new_id(),
                role_id=role.id,
                author="assistant",
                content=relay.texts.get("chat", "")[:16000],
                created_at=self._clock.now(),
            )
            await self._chats.add_message(principal.user_id, reply)
            yield Done(seq=relay.next_seq(), result_id=reply.id)
        except DomainError as error:
            yield Failed(seq=relay.next_seq(), error=error)
        finally:
            self._busy.discard((principal.user_id, role.id))


def _kit_block(role: RoleRecord) -> DataBlock:
    kit = role.kit
    if kit is None:
        return DataBlock("kit", "")
    values = {
        "cover_letter": kit.cover_note,
        "outreach": kit.outreach,
        "cv_note": kit.cv,
        "screening_answers": [{"q": a.q, "a": a.a} for a in kit.answers],
    }
    present = {key: value for key, value in values.items() if value}
    return DataBlock("kit", json.dumps(present, ensure_ascii=False) if present else "")
