"""PDF text extraction with pypdf (implements `domain.jobs.documents.PdfText`).

Uploads are untrusted. Extraction runs in a worker thread with a time limit and a cap on the
text kept, at most two at a time; pypdf's own decompression limits bound memory per stream.
Encrypted, broken or hostile files become `ValidationFailed` (422). Content is never logged.
"""

from __future__ import annotations

import asyncio
import io
import logging

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.domain.errors import FieldError, ValidationFailed
from app.domain.jobs.documents import PdfContent

MAX_TEXT_CHARS = 200_000
_PARSE_ERRORS = (
    PyPdfError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    RecursionError,
    OverflowError,
    ZeroDivisionError,
    MemoryError,
    UnicodeError,
    AssertionError,
)
# pypdf warns about malformed structure through `logging`; those messages can quote content.
logging.getLogger("pypdf").setLevel(logging.ERROR)


def _unreadable(reason: str) -> ValidationFailed:
    return ValidationFailed("That PDF couldn't be read.", errors=[FieldError("/data", reason)])


def extract_text(data: bytes, max_pages: int, max_chars: int = MAX_TEXT_CHARS) -> PdfContent:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _unreadable("Encrypted PDFs aren't supported; save an unprotected copy.")
        pages = len(reader.pages)
        if pages > max_pages:
            return PdfContent(pages=pages, text="")
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
        return PdfContent(pages=pages, text="\n".join(parts)[:max_chars])
    except ValidationFailed:
        raise
    except _PARSE_ERRORS:
        raise _unreadable("The file is damaged or not a readable PDF.") from None


class PypdfText:
    def __init__(self, timeout_s: float = 15.0, concurrency: int = 2) -> None:
        self._timeout = timeout_s
        self._slots = asyncio.Semaphore(concurrency)

    async def extract(self, data: bytes, max_pages: int) -> PdfContent:
        async with self._slots:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(extract_text, data, max_pages), self._timeout
                )
            except TimeoutError:
                raise _unreadable("The PDF took too long to read.") from None
