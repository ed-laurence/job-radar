# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "reportlab==4.2.5",
# ]
# ///
"""Render the demo persona's CVs (Alex Example) from seed/documents/*.json to PDFs beside them.

    uv run scripts/build_demo_cvs.py            # writes seed/documents/<slot>-cv.pdf
    uv run scripts/build_demo_cvs.py --check    # fails if a PDF is missing or out of date

The persona, employers and achievements are fictional (the employers match the seed's fake
companies). The JSON sources pass the seed PII gate (`build_seed.py --verify`); the PDFs carry
only that text. Output is deterministic (invariant mode, fixed metadata).

Each role renders as two lines that the CV-role parser relies on:
    <Title>
    <Employer> | <Location> | <Mon YYYY - Mon YYYY|Present>
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "seed" / "documents"
INK = colors.HexColor("#1d2433")
MUTED = colors.HexColor("#5b6475")

STYLES = {
    "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK),
    "headline": ParagraphStyle("headline", fontName="Helvetica", fontSize=11, leading=15, textColor=INK),
    "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9, leading=13, textColor=MUTED),
    "section": ParagraphStyle(
        "section", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=10,
        spaceAfter=4,
    ),
    "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK, spaceBefore=6),
    "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK, alignment=TA_LEFT),
    "tech": ParagraphStyle("tech", fontName="Helvetica-Oblique", fontSize=9, leading=12, textColor=MUTED),
}


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_esc(t), STYLES["body"]), leftIndent=10) for t in items],
        bulletType="bullet", start="-", leftIndent=10, bulletFontSize=9,
    )


def story(cv: dict[str, Any]) -> list[Any]:
    out: list[Any] = [
        Paragraph(_esc(cv["name"]), STYLES["name"]),
        Paragraph(_esc(cv["headline"]), STYLES["headline"]),
        Paragraph(_esc(cv["contact"]), STYLES["contact"]),
    ]
    for section in cv["sections"]:
        out.append(Paragraph(_esc(section["title"].upper()), STYLES["section"]))
        for para in section.get("paragraphs", []):
            out.append(Paragraph(_esc(para), STYLES["body"]))
        if section.get("items"):
            out.append(_bullets(section["items"]))
        for role in section.get("roles", []):
            out.append(Paragraph(_esc(role["title"]), STYLES["role"]))
            meta = " | ".join((role["employer"], role["location"], role["dates"]))
            out.append(Paragraph(_esc(meta), STYLES["meta"]))
            if role.get("blurb"):
                out.append(Paragraph(_esc(role["blurb"]), STYLES["body"]))
            if role.get("tech"):
                out.append(Paragraph(_esc("Core tech: " + role["tech"]), STYLES["tech"]))
            out.append(Spacer(1, 2))
            out.append(_bullets(role["bullets"]))
    return out


def render(cv: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
        bottomMargin=16 * mm, title=f"{cv['name']}: {cv['slot']} CV", author=cv["name"],
        subject="Demo CV (fictional persona)", creator="job-radar build_demo_cvs", invariant=1,
    )
    doc.build(story(cv))
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a PDF is missing or stale")
    args = parser.parse_args(argv)
    stale: list[str] = []
    for source in sorted(DOCS.glob("*-cv.json")):
        cv = json.loads(source.read_text(encoding="utf-8"))
        pdf = render(cv)
        target = source.with_suffix(".pdf")
        if args.check:
            if not target.exists() or target.read_bytes() != pdf:
                stale.append(target.name)
            continue
        target.write_bytes(pdf)
        print(f"wrote {target.relative_to(ROOT)} ({len(pdf):,} bytes)")
    if stale:
        print("out of date: " + ", ".join(stale) + " (run: uv run scripts/build_demo_cvs.py)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
