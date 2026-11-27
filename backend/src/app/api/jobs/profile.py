"""Profile documents, experience records and the LinkedIn connections import (spec tags
Settings and Contacts). Thin: parse, call one service, map."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Path, Request, Response, status

from app.api.jobs.deps import DefaultUser, Version, jobs, rate_limit_headers
from app.domain.jobs.documents import DocumentInfo
from app.domain.jobs.experience import EntryText, ExperienceDraft, ExperienceView
from app.generated.models import (
    ContactsDeleted,
    ContactsDeleteRequest,
    ContactsImportRequest,
    ContactsImportResult,
    ExperienceCreate,
    ExperienceList,
    ExperienceRecord,
    ExperienceUpdate,
    Last,
    LinkedinImportSummary,
    ProfileDocument,
    ProfileDocumentList,
    ProfileDocumentUpload,
)

router = APIRouter(prefix="/api")
Slot = Annotated[str, Path(max_length=16)]
ExperienceId = Annotated[uuid.UUID, Path(alias="experienceId")]
_NO_CONTENT = status.HTTP_204_NO_CONTENT


def _document(info: DocumentInfo) -> ProfileDocument:
    return ProfileDocument.model_validate(dataclasses.asdict(info))


def _experience(record: ExperienceView) -> ExperienceRecord:
    return ExperienceRecord.model_validate(dataclasses.asdict(record))


def _draft(body: ExperienceCreate | ExperienceUpdate) -> ExperienceDraft:
    return ExperienceDraft(
        requirement=body.requirement,
        entries=tuple(EntryText(context=e.context, detail=e.detail) for e in body.entries),
        role_id=body.role_id,
    )


# ------------------------------------------------------------------ documents


@router.get(
    "/profile/documents",
    operation_id="listProfileDocuments",
    tags=["Settings"],
    response_model=ProfileDocumentList,
)
async def list_profile_documents(request: Request, principal: DefaultUser) -> ProfileDocumentList:
    found = await jobs(request).documents.list_documents(principal)
    return ProfileDocumentList(items=[_document(info) for info in found])


@router.put(
    "/profile/documents/{slot}",
    operation_id="uploadProfileDocument",
    tags=["Settings"],
    response_model=ProfileDocument,
)
async def upload_profile_document(
    request: Request, principal: DefaultUser, slot: Slot, body: ProfileDocumentUpload
) -> ProfileDocument:
    documents = jobs(request).documents
    return _document(await documents.upload(principal, slot, body.filename, body.data))


@router.delete(
    "/profile/documents/{slot}",
    operation_id="deleteProfileDocument",
    tags=["Settings"],
    status_code=_NO_CONTENT,
)
async def delete_profile_document(request: Request, principal: DefaultUser, slot: Slot) -> Response:
    await jobs(request).documents.delete(principal, slot)
    return Response(status_code=_NO_CONTENT)


@router.get(
    "/profile/documents/{slot}/file",
    operation_id="getProfileDocumentFile",
    tags=["Settings"],
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_profile_document_file(
    request: Request, response: Response, principal: DefaultUser, slot: Slot
) -> Response:
    stored = await jobs(request).documents.download(principal, slot)
    name = quote(stored.filename or "document.pdf")
    return Response(
        content=stored.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{name}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            **rate_limit_headers(response),
        },
    )


@router.get(
    "/profile/linkedin-import",
    operation_id="getLinkedinImport",
    tags=["Settings"],
    response_model=LinkedinImportSummary,
    response_model_exclude_none=True,
)
async def get_linkedin_import(request: Request, principal: DefaultUser) -> LinkedinImportSummary:
    overview = await jobs(request).linkedin.overview(principal)
    last = None
    if overview.last is not None:
        last = Last.model_validate(dataclasses.asdict(overview.last))
    return LinkedinImportSummary(contacts=overview.contacts, last=last)


# ------------------------------------------------------------------ contacts import


@router.post(
    "/contacts/import",
    operation_id="importContacts",
    tags=["Contacts"],
    response_model=ContactsImportResult,
)
async def import_contacts(
    request: Request, principal: DefaultUser, body: ContactsImportRequest
) -> ContactsImportResult:
    result = await jobs(request).linkedin.import_csv(principal, body.filename, body.data)
    return ContactsImportResult.model_validate(dataclasses.asdict(result))


@router.delete(
    "/contacts",
    operation_id="deleteAllContacts",
    tags=["Contacts"],
    response_model=ContactsDeleted,
)
async def delete_all_contacts(
    request: Request, principal: DefaultUser, body: ContactsDeleteRequest
) -> ContactsDeleted:
    deleted = await jobs(request).linkedin.delete_all(principal, confirm=body.confirm)
    return ContactsDeleted(deleted=deleted)


# ------------------------------------------------------------------ experience


@router.get(
    "/experience",
    operation_id="listExperience",
    tags=["Settings"],
    response_model=ExperienceList,
    response_model_exclude_none=True,
)
async def list_experience(request: Request, principal: DefaultUser) -> ExperienceList:
    found = await jobs(request).experience.overview(principal)
    return ExperienceList.model_validate(
        {"items": [_experience(item) for item in found.items], "contexts": list(found.contexts)}
    )


@router.post(
    "/experience",
    operation_id="createExperience",
    tags=["Settings"],
    status_code=status.HTTP_201_CREATED,
    response_model=ExperienceRecord,
    response_model_exclude_none=True,
)
async def create_experience(
    request: Request, principal: DefaultUser, body: ExperienceCreate
) -> ExperienceRecord:
    return _experience(await jobs(request).experience.create(principal, _draft(body)))


@router.put(
    "/experience/{experienceId}",
    operation_id="updateExperience",
    tags=["Settings"],
    response_model=ExperienceRecord,
    response_model_exclude_none=True,
)
async def update_experience(
    request: Request, principal: DefaultUser, record_id: ExperienceId, body: ExperienceUpdate
) -> ExperienceRecord:
    service = jobs(request).experience
    return _experience(await service.update(principal, record_id, body.version, _draft(body)))


@router.delete(
    "/experience/{experienceId}",
    operation_id="deleteExperience",
    tags=["Settings"],
    status_code=_NO_CONTENT,
)
async def delete_experience(
    request: Request, principal: DefaultUser, record_id: ExperienceId, version: Version
) -> Response:
    await jobs(request).experience.delete(principal, record_id, version)
    return Response(status_code=_NO_CONTENT)
