"""Cards: CRUD, moves, history and triage (spec tag "Dev board"). Thin: parse, call one
domain service, map (ARCH-03). Responses omit absent fields ("no nulls")."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status
from fastapi.responses import PlainTextResponse

from app.api.devboard import mapping
from app.api.devboard.deps import AiUser, DefaultUser, Reviews, Services, actor
from app.domain.devboard.model import Column, MoveRequest, TaskEdit, TaskQuery
from app.domain.errors import NotFound
from app.generated.models import (
    Task,
    TaskColumn,
    TaskCreate,
    TaskDiff,
    TaskEventPage,
    TaskMove,
    TaskPage,
    TaskReview,
    TaskUpdate,
    TriageRequest,
    TriageResult,
)

router = APIRouter(prefix="/api", tags=["Dev board"])

TaskId = Annotated[uuid.UUID, Path(alias="taskId")]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=512)]
Version = Annotated[int, Query(ge=1)]
PATCH_TYPE = "text/x-patch"


@router.get(
    "/tasks", operation_id="listTasks", response_model=TaskPage, response_model_exclude_none=True
)
async def list_tasks(
    principal: DefaultUser,
    services: Services,
    column: Annotated[list[TaskColumn] | None, Query(max_length=11)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> TaskPage:
    columns = tuple(Column(value.value) for value in column or ())
    query = TaskQuery(columns=columns, limit=limit, cursor=cursor)
    page = await services.board.list(actor(principal), query)
    return TaskPage(
        items=[mapping.task(t, actor(principal)) for t in page.items], next_cursor=page.next_cursor
    )


@router.post(
    "/tasks",
    operation_id="createTask",
    response_model=Task,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_task(body: TaskCreate, principal: DefaultUser, services: Services) -> Task:
    labels = tuple(label.root for label in body.labels or ())
    created = await services.board.create(actor(principal), body.title, body.body, labels)
    return mapping.task(created, actor(principal))


@router.get(
    "/tasks/{taskId}", operation_id="getTask", response_model=Task, response_model_exclude_none=True
)
async def get_task(task_id: TaskId, principal: DefaultUser, services: Services) -> Task:
    viewer = actor(principal)
    return mapping.task(await services.board.get(viewer, task_id), viewer)


@router.patch(
    "/tasks/{taskId}",
    operation_id="updateTask",
    response_model=Task,
    response_model_exclude_none=True,
)
async def update_task(
    task_id: TaskId, body: TaskUpdate, principal: DefaultUser, services: Services
) -> Task:
    labels = None if body.labels is None else tuple(label.root for label in body.labels)
    edit = TaskEdit(version=body.version, title=body.title, body=body.body, labels=labels)
    viewer = actor(principal)
    return mapping.task(await services.board.update(viewer, task_id, edit), viewer)


@router.delete("/tasks/{taskId}", operation_id="deleteTask", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: TaskId, version: Version, principal: DefaultUser, services: Services
) -> Response:
    await services.board.delete(actor(principal), task_id, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tasks/{taskId}/move",
    operation_id="moveTask",
    response_model=Task,
    response_model_exclude_none=True,
)
async def move_task(
    task_id: TaskId, body: TaskMove, principal: DefaultUser, services: Services
) -> Task:
    move = MoveRequest(
        to_column=Column(body.to_column.value),
        rank=body.rank,
        version=body.version,
        reason=body.reason,
    )
    viewer = actor(principal)
    return mapping.task(await services.board.move(viewer, task_id, move), viewer)


@router.get(
    "/tasks/{taskId}/events",
    operation_id="listTaskEvents",
    response_model=TaskEventPage,
    response_model_exclude_none=True,
)
async def list_task_events(
    task_id: TaskId,
    principal: DefaultUser,
    services: Services,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> TaskEventPage:
    page = await services.board.events(actor(principal), task_id, cursor, limit)
    return TaskEventPage(
        items=[mapping.event(item) for item in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/tasks/{taskId}/triage",
    operation_id="getTaskTriage",
    response_model=TriageResult,
    response_model_exclude_none=True,
)
async def get_task_triage(
    task_id: TaskId, principal: DefaultUser, services: Services
) -> TriageResult:
    record = await services.board.latest_triage(actor(principal), task_id)
    if record is None:
        raise NotFound("This card hasn't been triaged yet.")
    return mapping.triage(record)


@router.post(
    "/tasks/{taskId}/triage",
    operation_id="retriageTask",
    response_model=TriageRequest,
    status_code=status.HTTP_202_ACCEPTED,
    response_model_exclude_none=True,
)
async def retriage_task(task_id: TaskId, principal: AiUser, services: Services) -> TriageRequest:
    run = await services.workflow.queue_triage(actor(principal), task_id)
    return TriageRequest(queued_at=run.created_at, run_id=run.id)


@router.get(
    "/tasks/{taskId}/review",
    operation_id="getTaskReview",
    response_model=TaskReview,
    response_model_exclude_none=True,
)
async def get_task_review(task_id: TaskId, principal: DefaultUser, service: Reviews) -> TaskReview:
    return mapping.review(await service.latest(actor(principal), task_id))


@router.get(
    "/tasks/{taskId}/diff",
    operation_id="getTaskDiff",
    response_model=TaskDiff,
    response_model_exclude_none=True,
)
async def get_task_diff(task_id: TaskId, principal: DefaultUser, service: Reviews) -> TaskDiff:
    return mapping.diff(await service.diff(actor(principal), task_id))


@router.get(
    "/tasks/{taskId}/patch",
    operation_id="getTaskPatch",
    response_class=PlainTextResponse,
    responses={200: {"content": {PATCH_TYPE: {}}}},
)
async def get_task_patch(task_id: TaskId, principal: DefaultUser, service: Reviews) -> Response:
    text = await service.patch(actor(principal), task_id)
    disposition = f'attachment; filename="task-{task_id}.patch"'
    return Response(text, media_type=PATCH_TYPE, headers={"Content-Disposition": disposition})
