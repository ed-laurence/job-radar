"""Scan routes (spec tag Scan): run a scan now, and its status. Maintainer only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.security import maintainer, rate_limited_user
from app.domain.identity import Principal
from app.domain.jobs.scan import MANUAL, ScanService
from app.domain.ports.scan import ScanRunRecord
from app.domain.rate_limits import RateTier
from app.generated.models import ScanRun, ScanRunStatus, ScanStatus, ScanTrigger

router = APIRouter(prefix="/api/scan", tags=["Scan"])

AgentMaintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.AGENT, maintainer))]
Maintainer = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT, maintainer))]


def _scan(request: Request) -> ScanService:
    found = request.app.state.scan
    assert isinstance(found, ScanService)
    return found


def scan_run(run: ScanRunRecord) -> ScanRun:
    return ScanRun(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        trigger=ScanTrigger(run.trigger),
        source=run.source,
        status=ScanRunStatus(run.status),
        fetched=run.fetched,
        new=run.new,
        duplicates=run.duplicates,
        scored=run.scored,
        failed=run.failed,
        pool_remaining=run.pool_remaining,
        note=run.note,
    )


@router.post("", operation_id="runScan", response_model=ScanRun)
async def run_scan(request: Request, principal: AgentMaintainer) -> ScanRun:
    del principal  # maintainer-only; the scan covers every account
    return scan_run(await _scan(request).run(MANUAL))


@router.get("/status", operation_id="getScanStatus", response_model=ScanStatus)
async def get_scan_status(request: Request, principal: Maintainer) -> ScanStatus:
    del principal
    view = await _scan(request).status()
    return ScanStatus(
        last_run=scan_run(view.last_run) if view.last_run is not None else None,
        next_run_at=view.next_run_at,
        interval_minutes=view.interval_minutes,
        pool_remaining=view.pool_remaining,
        running=view.running,
    )
