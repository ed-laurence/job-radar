"""CV-role parsing invariants over generated synthetic CVs: every role written in either layout
comes back, in order, once, and nothing outside the EXPERIENCE section is read."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.jobs.cv_roles import MAX_LABEL, cv_roles

_WORDS = st.sampled_from(
    ["Data", "Platform", "Lead", "Engineer", "Head", "Analytics", "Example", "Systems", "Co"]
)
_NAME = st.lists(_WORDS, min_size=1, max_size=4).map(" ".join)
_MONTHS = st.sampled_from(["", "Jan ", "Mar ", "Sep ", "December "])
_YEAR = st.integers(1990, 2026)


@st.composite
def _dates(draw: st.DrawFn) -> str:
    start = f"{draw(_MONTHS)}{draw(_YEAR)}"
    end = draw(st.one_of(st.just("Present"), _YEAR.map(str)))
    return f"{start} - {end}"


@st.composite
def _role(draw: st.DrawFn) -> tuple[str, str, list[str]]:
    title, employer = draw(_NAME), draw(_NAME)
    dates = draw(_dates())
    if draw(st.booleans()):
        lines = [title, f"{employer} | Melbourne VIC | {dates}"]
    else:
        lines = [title, employer, "Melbourne VIC", dates]
    bullets = draw(st.lists(_NAME.map(lambda text: f"- {text}"), max_size=3))
    return title, employer, lines + bullets


@settings(max_examples=200, deadline=None)
@given(roles=st.lists(_role(), max_size=8), noise=st.lists(_role(), max_size=3))
def test_every_role_in_the_section_is_found_in_order_once(
    roles: list[tuple[str, str, list[str]]], noise: list[tuple[str, str, list[str]]]
) -> None:
    before = [line for _, _, lines in noise for line in lines]
    body = [line for _, _, lines in roles for line in lines]
    text = "\n".join(["SUMMARY", *before, "EXPERIENCE", *body, "EDUCATION", *before])
    expected: list[str] = []
    for title, employer, _ in roles:
        label = f"{title}, {employer}"
        if label not in expected:
            expected.append(label)
    assert cv_roles(text) == expected


@settings(max_examples=200, deadline=None)
@given(text=st.text(max_size=2000))
def test_any_text_gives_unique_bounded_labels(text: str) -> None:
    found = cv_roles(text)
    assert len(found) == len(set(found))
    assert all(0 < len(label) <= MAX_LABEL and ", " in label for label in found)
