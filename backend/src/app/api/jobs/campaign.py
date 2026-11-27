"""Campaign plan, tasks and activity (spec tag Campaign) and main-page alerts (tag Alerts).
Thin: parse, call one domain service, map (ARCH-03)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status

from app.api.jobs.deps import DefaultUser, jobs
from app.domain.jobs.campaign import (
    ActivityDraft,
    ActivityListView,
    CheckpointView,
    PlanView,
    TaskListView,
)
from app.domain.jobs.records import ActivityRecord
from app.generated.models import (
    Alert,
    AlertList,
    CampaignActivity,
    CampaignActivityCreate,
    CampaignActivityList,
    CampaignCheckpoint,
    CampaignLink,
    CampaignMetric,
    CampaignPlan,
    CampaignTask,
    CampaignTaskList,
    CampaignTaskProgress,
)

router = APIRouter(prefix="/api")
Week = Annotated[int | None, Query(ge=1, le=52)]
RequiredWeek = Annotated[int, Query(ge=1, le=52)]
TaskId = Annotated[str, Path(alias="taskId", max_length=64, pattern=r"^[a-z0-9_-]{1,64}$")]
ActivityId = Annotated[uuid.UUID, Path(alias="activityId")]


def _checkpoint(view: CheckpointView) -> CampaignCheckpoint:
    return CampaignCheckpoint(week=view.week, text=view.text, due=view.due)


def _plan(view: PlanView) -> CampaignPlan:
    return CampaignPlan(
        name=view.name,
        start=view.start,
        end=view.end,
        total_weeks=view.total_weeks,
        current_week=view.current_week,
        week=view.week,
        week_start=view.week_start,
        week_end=view.week_end,
        metrics=[CampaignMetric.model_validate(m, from_attributes=True) for m in view.metrics],
        checkpoint=_checkpoint(view.checkpoint) if view.checkpoint else None,
        checkpoints=[_checkpoint(c) for c in view.checkpoints],
    )


def _tasks(view: TaskListView) -> CampaignTaskList:
    items = [
        CampaignTask(
            id=task.id,
            title=task.title,
            when=task.when,
            steps=task.steps,
            links=[CampaignLink.model_validate({"label": a, "url": b}) for a, b in task.links],
            done=task.done,
            progress=CampaignTaskProgress.model_validate(task.progress, from_attributes=True)
            if task.progress
            else None,
        )
        for task in view.items
    ]
    return CampaignTaskList(week=view.week, items=items)


def _activity(record: ActivityRecord) -> CampaignActivity:
    return CampaignActivity.model_validate(record, from_attributes=True)


def _activity_list(view: ActivityListView) -> CampaignActivityList:
    return CampaignActivityList(week=view.week, items=[_activity(r) for r in view.items])


@router.get(
    "/campaign",
    operation_id="getCampaign",
    tags=["Campaign"],
    response_model=CampaignPlan,
    response_model_exclude_none=True,
)
async def get_campaign(request: Request, principal: DefaultUser, week: Week = None) -> CampaignPlan:
    return _plan(await jobs(request).campaign.plan(principal, week))


@router.get(
    "/campaign/tasks",
    operation_id="listCampaignTasks",
    tags=["Campaign"],
    response_model=CampaignTaskList,
    response_model_exclude_none=True,
)
async def list_campaign_tasks(
    request: Request, principal: DefaultUser, week: Week = None
) -> CampaignTaskList:
    return _tasks(await jobs(request).campaign.tasks(principal, week))


@router.put(
    "/campaign/tasks/{taskId}/done",
    operation_id="markCampaignTaskDone",
    tags=["Campaign"],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mark_campaign_task_done(
    task_id: TaskId, request: Request, principal: DefaultUser, week: RequiredWeek
) -> Response:
    await jobs(request).campaign.set_task_done(principal, task_id, week, done=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/campaign/tasks/{taskId}/done",
    operation_id="clearCampaignTaskDone",
    tags=["Campaign"],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def clear_campaign_task_done(
    task_id: TaskId, request: Request, principal: DefaultUser, week: RequiredWeek
) -> Response:
    await jobs(request).campaign.set_task_done(principal, task_id, week, done=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/campaign/activity",
    operation_id="listCampaignActivity",
    tags=["Campaign"],
    response_model=CampaignActivityList,
    response_model_exclude_none=True,
)
async def list_campaign_activity(
    request: Request, principal: DefaultUser, week: Week = None
) -> CampaignActivityList:
    return _activity_list(await jobs(request).campaign.activity(principal, week))


@router.post(
    "/campaign/activity",
    operation_id="logCampaignActivity",
    tags=["Campaign"],
    response_model=CampaignActivity,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def log_campaign_activity(
    body: CampaignActivityCreate, request: Request, principal: DefaultUser
) -> CampaignActivity:
    draft = ActivityDraft(
        on_date=body.on_date,
        kind=body.kind.value,
        who=body.who,
        company=body.company,
        role_id=body.role_id,
        notes=body.notes,
    )
    return _activity(await jobs(request).campaign.log_activity(principal, draft))


@router.delete(
    "/campaign/activity/{activityId}",
    operation_id="deleteCampaignActivity",
    tags=["Campaign"],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_campaign_activity(
    activity_id: ActivityId, request: Request, principal: DefaultUser
) -> Response:
    await jobs(request).campaign.delete_activity(principal, activity_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts", operation_id="listAlerts", tags=["Alerts"], response_model=AlertList)
async def list_alerts(request: Request, principal: DefaultUser) -> AlertList:
    alerts = await jobs(request).main_page.alerts(principal)
    return AlertList(items=[Alert.model_validate(a, from_attributes=True) for a in alerts])
