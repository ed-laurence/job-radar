"""Cover letter PDFs with ReportLab (implements `domain.jobs.kit_files.LetterRenderer`), in the
layout of job-seeker's `letter_pdf.py`: A4, 2 cm margins, Helvetica; name 18 pt bold, contact
line 9.5 pt grey, body 10.5 pt on about 14 pt leading, a bold "Re:" line. One page: the body
type shrinks in steps (10.5, 10, 9.5, 9 pt) until it fits, and a letter longer than a page at
9 pt is kept rather than refused. Rendering runs in a worker thread; content is never logged.
"""

from __future__ import annotations

import asyncio
import io
from html import escape

from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate

from app.domain.jobs.kit_files import CONTACT_SEPARATOR, Letter

BODY_SIZES = (10.5, 10.0, 9.5, 9.0)
MARGIN = 2 * cm
_NBSP_SEPARATOR = "&nbsp;&nbsp;·&nbsp;&nbsp;"


def _text(value: str) -> str:
    return escape(value, quote=False)


def _build(letter: Letter, size: float) -> bytes:
    base = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=size, leading=size * 1.333, spaceAfter=size * 0.75
    )
    block = ParagraphStyle("block", parent=base, spaceAfter=7.5)
    flow: list[Flowable] = [
        Paragraph(
            _text(letter.name),
            ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=18, leading=22),
        ),
        Paragraph(
            _text(letter.contact).replace(CONTACT_SEPARATOR, _NBSP_SEPARATOR),
            ParagraphStyle(
                "contact",
                fontName="Helvetica",
                fontSize=9.5,
                leading=12,
                spaceBefore=2,
                spaceAfter=11,
                textColor=HexColor("#444444"),
            ),
        ),
        Paragraph(_text(letter.date), block),
        Paragraph(_text(letter.to), block),
        Paragraph(
            _text("Re: " + letter.re_line),
            ParagraphStyle("re", parent=block, fontName="Helvetica-Bold"),
        ),
        *(Paragraph(markup, base) for markup in letter.paragraphs),
    ]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=letter.title,
        author=letter.name,
    )
    document.build(flow)
    return buffer.getvalue()


def render_letter(letter: Letter) -> bytes:
    data = b""
    for size in BODY_SIZES:
        data = _build(letter, size)
        if len(PdfReader(io.BytesIO(data)).pages) == 1:
            return data
    return data


class ReportlabLetters:
    async def render(self, letter: Letter) -> bytes:
        return await asyncio.to_thread(render_letter, letter)
