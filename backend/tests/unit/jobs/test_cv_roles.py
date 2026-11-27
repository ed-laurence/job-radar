"""The roles on a CV, read from its extracted text. Synthetic CV lines only (the fictional
Alex Example)."""

from __future__ import annotations

import pytest

from app.domain.jobs.cv_roles import cv_roles

LAYOUT_A = """Alex Example
Principal Data Engineer | Python and GCP
SUMMARY
Engineer with a long delivery record. Worked 2019 - 2021 on this summary line.
EXPERIENCE
Principal Engineer
Example Digital Services | Melbourne VIC | Mar 2024 - Present
Consultancy building AI features.
- Built a claims-triage assistant; adopted by two teams
from Jan 2020 - Feb 2021 as a pilot.
Engineering Manager
Example Analytics | Melbourne VIC | Jan 2022 - Feb 2024
- Managed a team of six engineers.
Software Engineer
Example Control Systems | Jan 2009 - Jun 2012
EDUCATION
Bachelor of Engineering
Example University | Geelong VIC | 2004 - 2008
"""

EN_DASH = chr(0x2013)
LAYOUT_B = f"""EXPERIENCE
Head of Data
Example Learning
Melbourne VIC
Feb 2019 {EN_DASH} Dec 2021
- Set the data strategy.
Analytics Lead
Example Institute
Melbourne VIC
2016 - 2019
SKILLS
Python
"""


def test_layout_a_title_then_employer_location_dates() -> None:
    assert cv_roles(LAYOUT_A) == [
        "Principal Engineer, Example Digital Services",
        "Engineering Manager, Example Analytics",
        "Software Engineer, Example Control Systems",
    ]


def test_layout_b_separate_title_employer_location_and_dates_lines() -> None:
    assert cv_roles(LAYOUT_B) == [
        "Head of Data, Example Learning",
        "Analytics Lead, Example Institute",
    ]


def test_only_the_experience_section_counts() -> None:
    outside = "SUMMARY\nLead Engineer\nExample Co | Remote | 2019 - 2021\nSKILLS\nPython"
    assert cv_roles(outside) == []
    assert cv_roles("") == []


@pytest.mark.parametrize(
    "dates",
    ["Mar 2024 - Present", "Jan 2009 - Mar 2016", "2019 - 2021", "Sept 2020 to now"],
)
def test_date_range_forms(dates: str) -> None:
    text = f"WORK EXPERIENCE\nData Engineer\nExample Payments | {dates}\nEDUCATION"
    assert cv_roles(text) == ["Data Engineer, Example Payments"]


def test_duplicates_are_dropped_and_bullets_are_never_titles() -> None:
    text = (
        "EXPERIENCE\nData Engineer\nExample Payments | 2012 - 2016\n"
        "Data Engineer\nExample Payments | 2010 - 2012\n"
        "- Wrote pipelines\nExample Other | 2008 - 2010\n"
    )
    assert cv_roles(text) == ["Data Engineer, Example Payments"]


def test_whitespace_is_collapsed() -> None:
    text = "EXPERIENCE\n  Data   Engineer \n Example  Payments  | Victoria | 2012 - 2016"
    assert cv_roles(text) == ["Data Engineer, Example Payments"]
