"""Task chat (spec tag "Task chat"). Thin: parse, call one domain service, map (ARCH-03).

The reply streams as SSE with the kit/chat events plus `step` (ARCH-30): every frame comes from a
generated event model, and its text has already passed the redaction filter in the domain.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.devboard import chat_mapping
from app.api.devboard.deps import AiUser, DefaultUser, Services, actor
from app.api.jobs.sse import frame, generation_frame
from app.api.stream_guard import guard_stream
from app.domain.devboard.task_chat import ChatStep, TaskChatEvent
from app.domain.jobs.streams import Failed
from app.generated.models import (
    StepEvent,
    TaskChatMessageCreate,
    TaskChatMessagePage,
    TaskChatProposal,
    TaskChatProposalAccept,
    TaskChatProposalAccepted,
)

router = APIRouter(prefix="/api", tags=["Task chat"])

TaskId = Annotated[uuid.UUID, Path(alias="taskId")]
ProposalId = Annotated[uuid.UUID, Path(alias="proposalId")]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=512)]
_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
_SSE: dict[int | str, dict[str, object]] = {
    200: {"description": "TaskChatStreamEvent", "content": {"text/event-stream": {}}}
}


@router.get(
    "/tasks/{taskId}/chat",
    operation_id="listTaskChatMessages",
    response_model=TaskChatMessagePage,
    response_model_exclude_none=True,
)
async def list_task_chat_messages(
    task_id: TaskId,
    principal: DefaultUser,
    services: Services,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> TaskChatMessagePage:
    page = await services.chat.list_messages(actor(principal), task_id, cursor, limit)
    return chat_mapping.message_page(page)


@router.post(
    "/tasks/{taskId}/chat",
    operation_id="sendTaskChatMessage",
    response_class=StreamingResponse,
    responses=_SSE,
)
async def send_task_chat_message(
    task_id: TaskId,
    body: TaskChatMessageCreate,
    request: Request,
    principal: AiUser,
    services: Services,
) -> StreamingResponse:
    events = await services.chat.send(actor(principal), task_id, body.content)
    return StreamingResponse(
        guard_stream(request, _frames(request, events)),
        media_type="text/event-stream",
        headers=_HEADERS,
    )


@router.delete(
    "/tasks/{taskId}/chat", operation_id="clearTaskChat", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_task_chat(task_id: TaskId, principal: DefaultUser, services: Services) -> Response:
    await services.chat.clear(actor(principal), task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tasks/{taskId}/chat/proposals/{proposalId}/accept",
    operation_id="acceptTaskChatProposal",
    response_model=TaskChatProposalAccepted,
    response_model_exclude_none=True,
)
async def accept_task_chat_proposal(
    task_id: TaskId,
    proposal_id: ProposalId,
    body: TaskChatProposalAccept,
    principal: AiUser,
    services: Services,
) -> TaskChatProposalAccepted:
    outcome = await services.proposals.accept(actor(principal), task_id, proposal_id, body.version)
    return chat_mapping.accepted(outcome, actor(principal))


@router.post(
    "/tasks/{taskId}/chat/proposals/{proposalId}/reject",
    operation_id="rejectTaskChatProposal",
    response_model=TaskChatProposal,
    response_model_exclude_none=True,
)
async def reject_task_chat_proposal(
    task_id: TaskId, proposal_id: ProposalId, principal: DefaultUser, services: Services
) -> TaskChatProposal:
    record = await services.proposals.reject(actor(principal), task_id, proposal_id)
    return chat_mapping.proposal(record)


async def _frames(request: Request, events: AsyncIterator[TaskChatEvent]) -> AsyncIterator[str]:
    async for event in events:
        if isinstance(event, ChatStep):
            data = chat_mapping.step(event.step)
            yield frame(StepEvent(event="step", id=str(event.seq), data=data))
            continue
        yield generation_frame(request, event)
        if isinstance(event, Failed):
            return
