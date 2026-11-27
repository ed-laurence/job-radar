"""Roles and applications (spec tags Roles, Scoring). Thin: parse, call one service, map."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request, Response, status

from app.api.jobs import mapping
from app.api.jobs.deps import AiUser, Cursor, DefaultUser, Limit, Version, fields_of, jobs, plain
from app.domain.jobs.ports import RoleQuery
from app.domain.jobs.roles import RejectionInput, RoleDraft, StatusChange
from app.generated.models import (
    ArchiveOlderRequest,
    ArchiveResult,
    RankChange,
    Role,
    RoleCreate,
    RoleEventPage,
    RolePage,
    RoleStage,
    RoleStatus,
    RoleStatusChange,
    RoleUpdate,
    ScoringRequest,
    State,
    Track,
)

router = APIRouter(prefix="/api", tags=["Roles"])
RoleId = Annotated[uuid.UUID, Path(alias="roleId")]
Sort = Annotated[str, Query(pattern=r"^(rank|-found_at|-score|-posted_at)$", max_length=10)]
StatusFilter = Annotated[list[RoleStatus] | None, Query(alias="status", max_length=9)]
StageFilter = Annotated[list[RoleStage] | None, Query(max_length=7)]


@dataclass(frozen=True)
class _AgeFilter:
    min_days: int | None
    max_days: int | None


def _age_filter(
    min_age_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    max_age_days: Annotated[int | None, Query(ge=0, le=365)] = None,
) -> _AgeFilter:
    return _AgeFilter(min_age_days, max_age_days)


def _role_query(
    status_filter: StatusFilter = None,
    stage: StageFilter = None,
    track: Track | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Sort = "rank",
    age: Annotated[_AgeFilter | None, Depends(_age_filter)] = None,
) -> RoleQuery:
    return RoleQuery(
        statuses=tuple(item.value for item in status_filter or ()),
        stages=tuple(item.value for item in stage or ()),
        track=track.value if track else None,
        min_score=min_score,
        text=q or None,
        sort=sort,
        min_age_days=age.min_days if age else None,
        max_age_days=age.max_days if age else None,
    )


@router.get(
    "/roles", operation_id="listRoles", response_model=RolePage, response_model_exclude_none=True
)
async def list_roles(
    request: Request,
    principal: DefaultUser,
    query: Annotated[RoleQuery, Depends(_role_query)],
    cursor: Cursor = None,
    limit: Limit = 50,
) -> RolePage:
    return mapping.role_page(await jobs(request).roles.list_roles(principal, query, cursor, limit))


@router.post(
    "/roles",
    operation_id="createRole",
    status_code=status.HTTP_201_CREATED,
    response_model=Role,
    response_model_exclude_none=True,
)
async def create_role(
    body: RoleCreate, request: Request, principal: DefaultUser, background: BackgroundTasks
) -> Role:
    services = jobs(request)
    draft = RoleDraft(
        title=body.title,
        source=str(plain(body.source or "manual")),
        url=str(body.url) if body.url else None,
        company=body.company,
        location=body.location,
        work_mode=body.work_mode.value if body.work_mode else None,
        pay=body.pay,
        posted_at=body.posted_at,
        description=body.description,
        status=str(plain(body.status or "new")),
        notes=body.notes,
    )
    created = await services.roles.create_role(principal, draft)
    if body.score_now:
        background.add_task(services.scoring.score_in_background, principal, created.id)
    return mapping.role(created)


@router.post("/roles/archive-older", operation_id="archiveOlderRoles", response_model=ArchiveResult)
async def archive_older_roles(
    body: ArchiveOlderRequest, request: Request, principal: DefaultUser
) -> ArchiveResult:
    archived = await jobs(request).roles.archive_older(principal, body.days)
    return ArchiveResult(archived=archived)


@router.get(
    "/roles/{roleId}", operation_id="getRole", response_model=Role, response_model_exclude_none=True
)
async def get_role(role_id: RoleId, request: Request, principal: DefaultUser) -> Role:
    return mapping.role(await jobs(request).roles.get_role(principal, role_id))


@router.patch(
    "/roles/{roleId}",
    operation_id="updateRole",
    response_model=Role,
    response_model_exclude_none=True,
)
async def update_role(
    role_id: RoleId, body: RoleUpdate, request: Request, principal: DefaultUser
) -> Role:
    changes = fields_of(body, exclude=frozenset({"version"}))
    updated = await jobs(request).roles.update_role(principal, role_id, body.version, changes)
    return mapping.role(updated)


@router.delete("/roles/{roleId}", operation_id="deleteRole", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: RoleId, version: Version, request: Request, principal: DefaultUser
) -> Response:
    await jobs(request).roles.delete_role(principal, role_id, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/roles/{roleId}/status",
    operation_id="changeRoleStatus",
    response_model=Role,
    response_model_exclude_none=True,
)
async def change_role_status(
    role_id: RoleId, body: RoleStatusChange, request: Request, principal: DefaultUser
) -> Role:
    rejection = None
    if body.rejection is not None:
        rejection = RejectionInput(
            rejected_at=body.rejection.rejected_at,
            channel=body.rejection.channel.value if body.rejection.channel else None,
            reason_categories=tuple(c.value for c in body.rejection.reason_categories or ()),
        )
    change = StatusChange(
        status=body.status.value, version=body.version, rank=body.rank, rejection=rejection
    )
    return mapping.role(await jobs(request).roles.change_status(principal, role_id, change))


@router.post(
    "/roles/{roleId}/rank",
    operation_id="reorderRole",
    response_model=Role,
    response_model_exclude_none=True,
)
async def reorder_role(
    role_id: RoleId, body: RankChange, request: Request, principal: DefaultUser
) -> Role:
    moved = await jobs(request).roles.reorder(principal, role_id, body.rank, body.version)
    return mapping.role(moved)


@router.get(
    "/roles/{roleId}/events",
    operation_id="listRoleEvents",
    response_model=RoleEventPage,
    response_model_exclude_none=True,
)
async def list_role_events(
    role_id: RoleId,
    request: Request,
    principal: DefaultUser,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> RoleEventPage:
    page = await jobs(request).roles.list_events(principal, role_id, cursor, limit)
    return mapping.event_page(page)


@router.post(
    "/roles/{roleId}/score",
    operation_id="scoreRole",
    tags=["Scoring"],
    response_model=ScoringRequest,
    status_code=status.HTTP_202_ACCEPTED,
)
async def score_role(
    role_id: RoleId, request: Request, principal: AiUser, background: BackgroundTasks
) -> ScoringRequest:
    scoring = jobs(request).scoring
    ticket = await scoring.request_scoring(principal, role_id)
    background.add_task(scoring.score_in_background, principal, role_id)
    return ScoringRequest(
        role_id=ticket.role_id, state=State(ticket.state), requested_at=ticket.requested_at
    )
