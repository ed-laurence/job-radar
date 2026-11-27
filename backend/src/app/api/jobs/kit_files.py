"""Kit downloads (spec tag Kits): the cover letter and the CV for a role, as PDFs. Thin: parse,
call one service, stream the bytes. The content is never logged."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request, Response

from app.api.jobs.deps import DefaultUser, jobs, rate_limit_headers
from app.domain.jobs.kit_files import KitFile

router = APIRouter(prefix="/api")
RoleId = Annotated[uuid.UUID, Path(alias="roleId")]
Download = Annotated[bool, Query()]
_PDF: dict[int | str, dict[str, object]] = {200: {"content": {"application/pdf": {}}}}


def _pdf(file: KitFile, sent: Response, *, download: bool) -> Response:
    """The PDF, carrying the rate-limit headers the auth dependency set on `sent`."""
    disposition = "attachment" if download else "inline"
    name = file.filename
    return Response(
        content=file.data,
        media_type="application/pdf",
        headers={
            # The name is already ASCII-safe; filename* keeps it exact for every browser.
            "Content-Disposition": f'{disposition}; filename="{name}"; '
            f"filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            **rate_limit_headers(sent),
        },
    )


@router.get(
    "/roles/{roleId}/kit/cover-letter.pdf",
    operation_id="getKitCoverLetterPdf",
    tags=["Kits"],
    response_class=Response,
    responses=_PDF,
)
async def get_kit_cover_letter_pdf(
    request: Request,
    response: Response,
    principal: DefaultUser,
    role_id: RoleId,
    *,
    download: Download = True,
) -> Response:
    found = await jobs(request).kit_files.cover_letter(principal, role_id)
    return _pdf(found, response, download=download)


@router.get(
    "/roles/{roleId}/kit/cv.pdf",
    operation_id="getKitCvPdf",
    tags=["Kits"],
    response_class=Response,
    responses=_PDF,
)
async def get_kit_cv_pdf(
    request: Request,
    response: Response,
    principal: DefaultUser,
    role_id: RoleId,
    *,
    download: Download = True,
) -> Response:
    found = await jobs(request).kit_files.cv(principal, role_id)
    return _pdf(found, response, download=download)
