"""Tiny synthetic PDFs for upload tests (no real CVs: every line is made up)."""

from __future__ import annotations

import base64
import io

from pypdf import PdfReader, PdfWriter

SAMPLE_CV = (
    "Summary",
    "Platform engineer who builds data pipelines.",
    "Experience",
    "Senior Engineer, Example Pty Ltd, 2019 - Present",
    "- Built the event pipeline",
    "- Ran the on-call rota",
    "- Cut cloud spend by a third",
    "Skills",
    "Python, SQL, Terraform",
)


def _escape(text: str) -> str:
    backslash = chr(92)
    return (
        text.replace(backslash, backslash * 2)
        .replace("(", backslash + "(")
        .replace(")", backslash + ")")
    )


def make_pdf(lines: tuple[str, ...] = SAMPLE_CV, pages: int = 1) -> bytes:
    """A valid PDF with `lines` on the first page and `pages` pages in total."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index in range(pages):
        content_id = page_ids[index] + 1
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        shown = lines if index == 0 else (f"Page {index + 1}",)
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        ops += [f"({_escape(line)}) Tj T*" for line in shown]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf())))
    writer.encrypt(user_password="secret-pw", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
