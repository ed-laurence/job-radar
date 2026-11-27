"""Contacts and campaign targets (spec tags Contacts, Targets)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.jobs import mapping
from app.api.jobs.deps import Cursor, DefaultUser, Limit, Version, fields_of, jobs
from app.generated.models import (
    Contact,
    ContactCreate,
    ContactPage,
    ContactStatus,
    ContactUpdate,
    Target,
    TargetCreate,
    TargetLane,
    TargetPage,
    TargetStatus,
    TargetUpdate,
)

router = APIRouter(prefix="/api")
ContactId = Annotated[uuid.UUID, Path(alias="contactId")]
TargetId = Annotated[uuid.UUID, Path(alias="targetId")]
Search = Annotated[str | None, Query(max_length=100)]
_NO_CONTENT = status.HTTP_204_NO_CONTENT


@dataclass(frozen=True)
class ContactFilters:
    status: str | None
    company: str | None
    needs_outreach: bool
    q: str | None


def contact_filters(
    status_filter: Annotated[ContactStatus | None, Query(alias="status")] = None,
    company: Annotated[str | None, Query(max_length=200)] = None,
    *,
    needs_outreach: Annotated[bool, Query()] = False,
    q: Search = None,
) -> ContactFilters:
    """`listContacts`' filters, gathered so the route stays within the argument limit."""
    chosen = status_filter.value if status_filter else None
    return ContactFilters(chosen, company, needs_outreach, q)


@router.get(
    "/contacts",
    operation_id="listContacts",
    tags=["Contacts"],
    response_model=ContactPage,
    response_model_exclude_none=True,
)
async def list_contacts(
    request: Request,
    principal: DefaultUser,
    filters: Annotated[ContactFilters, Depends(contact_filters)],
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ContactPage:
    page = await jobs(request).contacts.list_contacts(
        principal,
        filters.status,
        filters.company,
        cursor,
        limit,
        needs_outreach=filters.needs_outreach,
        q=filters.q,
    )
    return mapping.contact_page(page)


@router.post(
    "/contacts",
    operation_id="createContact",
    tags=["Contacts"],
    response_model=Contact,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(body: ContactCreate, request: Request, principal: DefaultUser) -> Contact:
    created = await jobs(request).contacts.create_contact(principal, fields_of(body))
    return mapping.contact(created)


@router.get(
    "/contacts/{contactId}",
    operation_id="getContact",
    tags=["Contacts"],
    response_model=Contact,
    response_model_exclude_none=True,
)
async def get_contact(contact_id: ContactId, request: Request, principal: DefaultUser) -> Contact:
    return mapping.contact(await jobs(request).contacts.get_contact(principal, contact_id))


@router.patch(
    "/contacts/{contactId}",
    operation_id="updateContact",
    tags=["Contacts"],
    response_model=Contact,
    response_model_exclude_none=True,
)
async def update_contact(
    contact_id: ContactId, body: ContactUpdate, request: Request, principal: DefaultUser
) -> Contact:
    changes = fields_of(body, exclude=frozenset({"version"}))
    updated = await jobs(request).contacts.update_contact(
        principal, contact_id, body.version, changes
    )
    return mapping.contact(updated)


@router.delete(
    "/contacts/{contactId}",
    operation_id="deleteContact",
    tags=["Contacts"],
    status_code=_NO_CONTENT,
)
async def delete_contact(
    contact_id: ContactId, version: Version, request: Request, principal: DefaultUser
) -> Response:
    await jobs(request).contacts.delete_contact(principal, contact_id, version)
    return Response(status_code=_NO_CONTENT)


@router.get(
    "/targets",
    operation_id="listTargets",
    tags=["Targets"],
    response_model=TargetPage,
    response_model_exclude_none=True,
)
async def list_targets(
    request: Request,
    principal: DefaultUser,
    cursor: Cursor = None,
    limit: Limit = 50,
    status_filter: Annotated[TargetStatus | None, Query(alias="status")] = None,
    lane: TargetLane | None = None,
    q: Search = None,
) -> TargetPage:
    page = await jobs(request).targets.list_targets(
        principal,
        status_filter.value if status_filter else None,
        lane.value if lane else None,
        cursor,
        limit,
        q=q,
    )
    return mapping.target_page(page)


@router.post(
    "/targets",
    operation_id="createTarget",
    tags=["Targets"],
    response_model=Target,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_target(body: TargetCreate, request: Request, principal: DefaultUser) -> Target:
    return mapping.target(await jobs(request).targets.create_target(principal, fields_of(body)))


@router.get(
    "/targets/{targetId}",
    operation_id="getTarget",
    tags=["Targets"],
    response_model=Target,
    response_model_exclude_none=True,
)
async def get_target(target_id: TargetId, request: Request, principal: DefaultUser) -> Target:
    return mapping.target(await jobs(request).targets.get_target(principal, target_id))


@router.patch(
    "/targets/{targetId}",
    operation_id="updateTarget",
    tags=["Targets"],
    response_model=Target,
    response_model_exclude_none=True,
)
async def update_target(
    target_id: TargetId, body: TargetUpdate, request: Request, principal: DefaultUser
) -> Target:
    changes = fields_of(body, exclude=frozenset({"version"}))
    updated = await jobs(request).targets.update_target(principal, target_id, body.version, changes)
    return mapping.target(updated)


@router.delete(
    "/targets/{targetId}", operation_id="deleteTarget", tags=["Targets"], status_code=_NO_CONTENT
)
async def delete_target(
    target_id: TargetId, version: Version, request: Request, principal: DefaultUser
) -> Response:
    await jobs(request).targets.delete_target(principal, target_id, version)
    return Response(status_code=_NO_CONTENT)
