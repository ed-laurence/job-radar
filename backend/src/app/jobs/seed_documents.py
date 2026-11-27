"""The demo CVs (`seed/documents/{technical,leadership}-cv.pdf`, rendered by
`scripts/build_demo_cvs.py` for the fictional Alex Example) loaded into the owner's profile
documents. They go through `ProfileDocumentService.upload`, the same path as a user's upload
(PDF checks, content guard, PII de-identification, storage and the manifest). Idempotent: a
slot that already holds a document is left alone, so a re-run (or the user's own upload) is
never replaced."""

from __future__ import annotations

import base64
from pathlib import Path

from app.domain.identity import Principal
from app.domain.jobs.documents import ProfileDocumentService

SEED_DOCUMENTS = (("technical", "technical-cv.pdf"), ("leadership", "leadership-cv.pdf"))


async def seed_documents(
    service: ProfileDocumentService, principal: Principal, folder: Path
) -> list[str]:
    """Upload each demo CV whose slot is empty. Returns the slots written."""
    present = {info.slot for info in await service.list_documents(principal)}
    written: list[str] = []
    for slot, name in SEED_DOCUMENTS:
        path = folder / name
        if slot in present or not path.is_file():
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        await service.upload(principal, slot, name, data)
        written.append(slot)
    return written
