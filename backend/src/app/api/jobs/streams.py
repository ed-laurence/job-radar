"""Kits and chat (SSE), and the research agent (spec tags Kits, Chat, Research)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Path, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.jobs import mapping
from app.api.jobs.deps import AgentUser, AiUser, Cursor, DefaultUser, Limit, StreamUser, jobs
from app.api.jobs.sse import generation_stream, step_stream
from app.domain.jobs.kits import PARTS, KitRequestInput
from app.generated.models import (
    ChatMessageCreate,
    ChatMessagePage,
    GenerationStreamEvent,
    KitRequest,
    ResearchRun,
    ResearchRunCreate,
    StepStreamEvent,
)

router = APIRouter(prefix="/api")
RoleId = Annotated[uuid.UUID, Path(alias="roleId")]
TargetId = Annotated[uuid.UUID, Path(alias="targetId")]
RunId = Annotated[uuid.UUID, Path(alias="researchRunId")]
LastEventId = Annotated[
    str | None, Header(alias="Last-Event-ID", pattern=r"^[0-9]{1,12}$", max_length=64)
]


def _sse(
    model: type[GenerationStreamEvent | StepStreamEvent],
) -> dict[int | str, dict[str, object]]:
    # The event schemas live in the spec; this only documents the media type.
    return {200: {"description": model.__name__, "content": {"text/event-stream": {}}}}


@router.post(
    "/roles/{roleId}/kit",
    operation_id="generateKit",
    tags=["Kits"],
    response_class=StreamingResponse,
    responses=_sse(GenerationStreamEvent),
)
async def generate_kit(
    role_id: RoleId, request: Request, principal: AiUser, body: KitRequest | None = None
) -> StreamingResponse:
    given = body or KitRequest(parts=None, questions=None)
    kit_request = KitRequestInput(
        parts=tuple(part.value for part in given.parts or ()) or PARTS,
        questions=tuple(question.root for question in given.questions or ()),
    )
    events = await jobs(request).kits.open_kit(principal, role_id, kit_request)
    return generation_stream(request, events)


@router.get(
    "/roles/{roleId}/chat/messages",
    operation_id="listChatMessages",
    tags=["Chat"],
    response_model=ChatMessagePage,
    response_model_exclude_none=True,
)
async def list_chat_messages(
    role_id: RoleId,
    request: Request,
    principal: DefaultUser,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ChatMessagePage:
    page = await jobs(request).chat.list_messages(principal, role_id, cursor, limit)
    return mapping.chat_page(page)


@router.post(
    "/roles/{roleId}/chat/messages",
    operation_id="sendChatMessage",
    tags=["Chat"],
    response_class=StreamingResponse,
    responses=_sse(GenerationStreamEvent),
)
async def send_chat_message(
    role_id: RoleId, body: ChatMessageCreate, request: Request, principal: AiUser
) -> StreamingResponse:
    events = await jobs(request).chat.send(principal, role_id, body.content)
    return generation_stream(request, events)


@router.delete(
    "/roles/{roleId}/chat/messages",
    operation_id="clearChat",
    tags=["Chat"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_chat(role_id: RoleId, request: Request, principal: DefaultUser) -> Response:
    await jobs(request).chat.clear(principal, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/targets/{targetId}/research-runs",
    operation_id="startResearchRun",
    tags=["Research"],
    response_model=ResearchRun,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_research_run(
    target_id: TargetId,
    request: Request,
    principal: AgentUser,
    background: BackgroundTasks,
    body: ResearchRunCreate | None = None,
) -> ResearchRun:
    research = jobs(request).research
    focus = tuple(item.value for item in (body.focus if body and body.focus else ()))
    run = await research.start(principal, target_id, focus)
    background.add_task(research.execute, principal, run.id)
    return mapping.research_run(run)


@router.get(
    "/research-runs/{researchRunId}",
    operation_id="getResearchRun",
    tags=["Research"],
    response_model=ResearchRun,
    response_model_exclude_none=True,
)
async def get_research_run(run_id: RunId, request: Request, principal: DefaultUser) -> ResearchRun:
    return mapping.research_run(await jobs(request).research.get_run(principal, run_id))


@router.get(
    "/research-runs/{researchRunId}/steps",
    operation_id="streamResearchSteps",
    tags=["Research"],
    response_class=StreamingResponse,
    responses=_sse(StepStreamEvent),
)
async def stream_research_steps(
    run_id: RunId, request: Request, principal: StreamUser, last_event_id: LastEventId = None
) -> StreamingResponse:
    after = int(last_event_id) if last_event_id else 0
    events = await jobs(request).research.open_steps(principal, run_id, after)
    return step_stream(request, events)
