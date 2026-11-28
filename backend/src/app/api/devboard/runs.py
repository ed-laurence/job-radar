"""Plans, approval, the kill switch, runs and the step stream (spec tags "Dev board", "Runs").

The stream is SSE: saved steps after `Last-Event-ID`, then live ones, then `done` (or
`error`). Step text was redacted before it was stored and passes the redaction filter again
on the way out (ARCH-30, SEC-06); a step that can't be checked shows `[redacted]`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.devboard import mapping
from app.api.devboard.deps import (
    AgentMaintainer,
    DefaultMaintainer,
    DefaultUser,
    DevboardServices,
    Services,
    StreamUser,
    actor,
)
from app.api.problems import correlation_id
from app.api.stream_guard import guard_stream
from app.domain.devboard.model import Actor, RunStatus
from app.domain.devboard.runs import StreamEnd
from app.domain.errors import DomainError
from app.generated.models import (
    Data2,
    DoneEvent,
    ErrorEvent,
    KillRequest,
    Plan,
    PlanApproval,
    PlanRequest,
    Problem,
    Run,
    RunPage,
    StepEvent,
    Task,
)

router = APIRouter(prefix="/api")

TaskId = Annotated[uuid.UUID, Path(alias="taskId")]
RunId = Annotated[uuid.UUID, Path(alias="runId")]
REDACTED = "[redacted]"


@router.get(
    "/tasks/{taskId}/plan",
    operation_id="getTaskPlan",
    response_model=Plan,
    response_model_exclude_none=True,
    tags=["Dev board"],
)
async def get_task_plan(task_id: TaskId, principal: DefaultUser, services: Services) -> Plan:
    return mapping.plan(await services.plans.get(actor(principal), task_id))


@router.post(
    "/tasks/{taskId}/plan",
    operation_id="requestTaskPlan",
    response_model=PlanRequest,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Dev board"],
)
async def request_task_plan(
    task_id: TaskId, principal: AgentMaintainer, services: Services
) -> PlanRequest:
    run = await services.workflow.request_plan(actor(principal), task_id)
    return PlanRequest(queued_at=run.created_at, run_id=run.id)


@router.post(
    "/tasks/{taskId}/plan/approve",
    operation_id="approveTaskPlan",
    response_model=Task,
    response_model_exclude_none=True,
    tags=["Dev board"],
)
async def approve_task_plan(
    task_id: TaskId, body: PlanApproval, principal: DefaultMaintainer, services: Services
) -> Task:
    approved = await services.plans.approve(
        actor(principal),
        task_id,
        plan_version=body.plan_version,
        task_version=body.task_version,
        rank=body.rank,
        allow_test_changes=body.allow_test_changes is True,
    )
    return mapping.task(approved, actor(principal))


@router.post(
    "/tasks/{taskId}/kill",
    operation_id="killTask",
    response_model=Task,
    response_model_exclude_none=True,
    tags=["Dev board"],
)
async def kill_task(
    task_id: TaskId, body: KillRequest, principal: AgentMaintainer, services: Services
) -> Task:
    killed = await services.workflow.kill(actor(principal), task_id, body.version, body.reason)
    return mapping.task(killed, actor(principal))


@router.get(
    "/tasks/{taskId}/runs",
    operation_id="listTaskRuns",
    response_model=RunPage,
    response_model_exclude_none=True,
    tags=["Runs"],
)
async def list_task_runs(
    task_id: TaskId,
    principal: DefaultUser,
    services: Services,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RunPage:
    page = await services.runs.for_task(actor(principal), task_id, cursor, limit)
    return RunPage(items=[mapping.run(r) for r in page.items], next_cursor=page.next_cursor)


@router.get(
    "/runs/{runId}",
    operation_id="getRun",
    response_model=Run,
    response_model_exclude_none=True,
    tags=["Runs"],
)
async def get_run(run_id: RunId, principal: DefaultUser, services: Services) -> Run:
    return mapping.run(await services.runs.get(actor(principal), run_id))


@router.get(
    "/runs/{runId}/steps",
    operation_id="streamRunSteps",
    response_class=StreamingResponse,
    tags=["Runs"],
)
async def stream_run_steps(
    run_id: RunId,
    request: Request,
    principal: StreamUser,
    services: Services,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID", max_length=64, pattern=r"^[0-9]{1,12}$")
    ] = None,
) -> StreamingResponse:
    caller = actor(principal)
    await services.runs.get(caller, run_id)
    after = int(last_event_id) if last_event_id is not None else 0
    body = _events(services, caller, run_id, after, correlation_id(request))
    return StreamingResponse(
        guard_stream(request, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _events(
    services: DevboardServices, caller: Actor, run_id: uuid.UUID, after: int, cid: str
) -> AsyncIterator[str]:
    try:
        async for item in services.runs.stream(caller, run_id, after):
            if isinstance(item, StreamEnd):
                yield _frame(_done(item))
                return
            summary = await services.redactor.redact_or_none(item.summary)
            event = StepEvent(
                event="step", id=str(item.seq), data=mapping.step(item, summary or REDACTED)
            )
            yield _frame(event)
    except DomainError as error:
        yield _frame(_error(error, cid))


def _done(end: StreamEnd) -> DoneEvent:
    status_text = end.run.status.value if end.run.status is not RunStatus.QUEUED else None
    return DoneEvent(event="done", id="done", data=Data2(result_id=end.run.id, status=status_text))


_STREAM_STATUS = {"not-found": 404, "forbidden": 403, "upstream-unavailable": 503}


def _error(error: DomainError, cid: str) -> ErrorEvent:
    problem = Problem.model_validate(
        {
            "type": f"/problems/{error.code}",
            "title": "The stream stopped",
            "status": _STREAM_STATUS.get(error.code, 500),
            "detail": (error.detail or None),
            "correlation_id": cid,
        }
    )
    return ErrorEvent(event="error", id="error", data=problem)


def _frame(event: StepEvent | DoneEvent | ErrorEvent) -> str:
    data = event.data.model_dump_json(exclude_none=True)
    return f"event: {event.event}\nid: {event.id}\ndata: {data}\n\n"
