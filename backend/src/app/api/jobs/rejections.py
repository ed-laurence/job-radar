"""Rejection analysis, rejection patterns and follow-up drafts (spec tags Rejections and
Follow-ups). Thin: parse, call one domain service, map (ARCH-03)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse

from app.api.jobs import mapping
from app.api.jobs.deps import AiUser, Cursor, DefaultUser, Limit, jobs
from app.api.jobs.sse import generation_stream
from app.generated.models import (
    GenerationStreamEvent,
    ReasonStatus,
    RejectionAnalysis,
    RejectionCategory,
    RejectionPatterns,
    RejectionSummary,
    RejectionSummaryPage,
)

router = APIRouter(prefix="/api")
RoleId = Annotated[uuid.UUID, Path(alias="roleId")]


@router.post(
    "/roles/{roleId}/rejection-analysis",
    operation_id="analyseRejection",
    tags=["Rejections"],
    response_model=RejectionAnalysis,
    response_model_exclude_none=True,
)
async def analyse_rejection(
    role_id: RoleId, request: Request, principal: AiUser
) -> RejectionAnalysis:
    record = await jobs(request).rejections.analyse(principal, role_id)
    return mapping.rejection_analysis(record)


@router.get(
    "/rejections/patterns",
    operation_id="getRejectionPatterns",
    tags=["Rejections"],
    response_model=RejectionPatterns,
)
async def get_rejection_patterns(request: Request, principal: DefaultUser) -> RejectionPatterns:
    return mapping.rejection_patterns(await jobs(request).rejections.patterns(principal))


@router.get(
    "/rejections",
    operation_id="listRejections",
    tags=["Rejections"],
    response_model=RejectionSummaryPage,
    response_model_exclude_none=True,
)
async def list_rejections(
    request: Request,
    principal: DefaultUser,
    cursor: Cursor = None,
    limit: Limit = 50,
    reason_status: Annotated[ReasonStatus | None, Query()] = None,
) -> RejectionSummaryPage:
    page = await jobs(request).main_page.rejections(
        principal, reason_status.value if reason_status else None, cursor, limit
    )
    items = [
        RejectionSummary(
            role_id=view.record.role_id,
            title=view.record.title,
            company=view.record.company,
            rejected_at=view.record.rejected_at,
            reason_status=ReasonStatus(view.reason_status),
            summary=view.record.summary,
            categories=[RejectionCategory(c) for c in view.record.categories] or None,
        )
        for view in page.items
    ]
    return RejectionSummaryPage(items=items, next_cursor=page.next_cursor)


@router.post(
    "/roles/{roleId}/followups/draft",
    operation_id="draftFollowups",
    tags=["Follow-ups"],
    response_class=StreamingResponse,
    responses={
        200: {
            "description": GenerationStreamEvent.__name__,
            "content": {"text/event-stream": {}},
        }
    },
)
async def draft_followups(
    role_id: RoleId, request: Request, principal: AiUser
) -> StreamingResponse:
    events = await jobs(request).followup_stream.open_draft(principal, role_id)
    return generation_stream(request, events)
