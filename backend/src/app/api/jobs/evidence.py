"""The full evidence test (spec tag Scoring): the latest result, and "Run again". Thin: parse,
call the domain service, map (ARCH-03). "I've done this" needs no endpoint of its own: the web
saves an experience record (`createExperience`) and then calls `runRoleEvidence`."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Path, Request

from app.api.jobs.deps import AiUser, DefaultUser, jobs
from app.domain.jobs.evidence_test import EvidenceTestRecord
from app.generated.models import RoleEvidenceTest

router = APIRouter(prefix="/api")
RoleId = Annotated[uuid.UUID, Path(alias="roleId")]


def _model(record: EvidenceTestRecord) -> RoleEvidenceTest:
    return RoleEvidenceTest.model_validate(asdict(record))


@router.get(
    "/roles/{roleId}/evidence",
    operation_id="getRoleEvidence",
    tags=["Scoring"],
    response_model=RoleEvidenceTest,
    response_model_exclude_none=True,
)
async def get_role_evidence(
    role_id: RoleId, request: Request, principal: DefaultUser
) -> RoleEvidenceTest:
    return _model(await jobs(request).evidence.latest(principal, role_id))


@router.post(
    "/roles/{roleId}/evidence",
    operation_id="runRoleEvidence",
    tags=["Scoring"],
    response_model=RoleEvidenceTest,
    response_model_exclude_none=True,
)
async def run_role_evidence(
    role_id: RoleId, request: Request, principal: AiUser
) -> RoleEvidenceTest:
    return _model(await jobs(request).evidence.run(principal, role_id))
