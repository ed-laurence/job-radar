"""Pure logic behind the Settings profile sections: LinkedIn CSV parsing, readability checks,
file-name sanitising, scoring weights and their effect on the prompt and the breakdown, PDF
extraction limits and the roles age filter."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest

from app.adapters.fakes.jobs import MemoryRoles
from app.adapters.fakes.security import FakeContentGuard
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.adapters.pdf import extract_text
from app.domain.errors import PayloadTooLarge, ValidationFailed
from app.domain.identity import AppRole, Principal
from app.domain.jobs.documents import decode_upload, readability, sanitise_filename
from app.domain.jobs.linkedin import MAX_ROWS, neutralise, parse_connections_csv, parse_date
from app.domain.jobs.ports import RoleQuery
from app.domain.jobs.roles import RoleDraft, RoleService
from app.domain.jobs.scoring import (
    ASSESS_INSTRUCTIONS,
    Assessment,
    Breakdown,
    assess_instructions,
    preferences_block,
    scored_values,
)
from app.domain.user_settings import (
    DEFAULTS,
    ScoreWeights,
    ScoringPreferences,
    check_scoring_preferences,
)
from tests.pdfs import encrypted_pdf, make_pdf

PREAMBLE = (
    "Notes:\n"
    '"When exporting your connection data, you may notice that some of the email addresses '
    'are missing."\n'
    "\n"
)
HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"


def _csv(*rows: str) -> str:
    return PREAMBLE + HEADER + "".join(f"{row}\n" for row in rows)


# ------------------------------------------------------------------ CSV


def test_csv_skips_the_preamble_and_maps_columns() -> None:
    parsed = parse_connections_csv(
        _csv(
            "Alex,Example,https://www.linkedin.com/in/alex-example,alex@example.test,"
            "Acme Pty Ltd,Data Lead,26 Nov 2026"
        )
    )
    assert parsed.skipped == 0
    (row,) = parsed.rows
    assert (row.first_name, row.last_name, row.email) == ("Alex", "Example", "alex@example.test")
    assert row.url == "https://www.linkedin.com/in/alex-example"
    assert (row.company, row.position) == ("Acme Pty Ltd", "Data Lead")
    assert row.connected_on == date(2026, 11, 26)


def test_csv_handles_quotes_commas_and_newlines() -> None:
    parsed = parse_connections_csv(
        _csv(
            'Sam,"O\'Example, Jr",https://www.linkedin.com/in/sam,,"Widgets, Inc",'
            '"Head of ""Data""\nand AI",01 Jan 2025'
        )
    )
    (row,) = parsed.rows
    assert row.last_name == "O'Example, Jr"
    assert row.company == "Widgets, Inc"
    assert row.position == 'Head of "Data"\nand AI'
    assert row.email is None


def test_csv_neutralises_formula_cells() -> None:
    parsed = parse_connections_csv(
        _csv(
            '=HYPERLINK("http://x.test"),+1,https://www.linkedin.com/in/a,@bad,'
            "-2+3,@SUM(A1),1 Feb 2025"
        )
    )
    (row,) = parsed.rows
    assert row.first_name == '\'=HYPERLINK("http://x.test")'
    assert row.last_name == "'+1"
    assert row.company == "'-2+3"
    assert row.position == "'@SUM(A1)"
    assert row.email is None  # "@bad" is not an address


@pytest.mark.parametrize("cell", ["=1", "+1", "-1", "@1", "\t=1", "  =1"])
def test_neutralise_prefixes_dangerous_starts(cell: str) -> None:
    assert neutralise(cell).startswith("'")


def test_neutralise_leaves_plain_text() -> None:
    assert neutralise("  Acme  ") == "Acme"


def test_csv_skips_rows_without_name_or_http_url_and_dedupes() -> None:
    parsed = parse_connections_csv(
        _csv(
            ",,https://www.linkedin.com/in/nobody,,,,",
            "Kim,Example,javascript:alert(1),,,,",
            "Kim,Example,,,,,",
            "Lee,Example,https://www.linkedin.com/in/lee,,Old Co,,",
            "Lee,Example,https://www.linkedin.com/in/lee,,New Co,,",
        )
    )
    assert parsed.skipped == 3
    assert [row.company for row in parsed.rows] == ["New Co"]


def test_csv_without_a_header_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        parse_connections_csv("just,some,text\n1,2,3\n")


def test_csv_row_cap() -> None:
    rows = [f"P{i},Example,https://www.linkedin.com/in/p{i},,,," for i in range(MAX_ROWS + 1)]
    with pytest.raises(ValidationFailed):
        parse_connections_csv(_csv(*rows))


def test_dates_in_linkedin_formats() -> None:
    assert parse_date("03 Mar 2024") == date(2024, 3, 3)
    assert parse_date("2024-03-03") == date(2024, 3, 3)
    assert parse_date("soon") is None


def test_upload_decoding_limits() -> None:
    assert decode_upload(base64.b64encode(b"abc").decode(), 10) == b"abc"
    with pytest.raises(PayloadTooLarge):
        decode_upload(base64.b64encode(b"x" * 11).decode(), 10)
    with pytest.raises(ValidationFailed):
        decode_upload("not base64!!", 10)


# ------------------------------------------------------------------ readability, names


def test_readability_counts_sections_roles_and_bullets() -> None:
    text = "\n".join(
        (
            "Summary",
            "Experience",
            "Engineer, Example Co, 2019 - 2022",
            "Lead, Other Co, 2022 \u2013 Present",
            "\u2022 Built things",
            "- Ran things",
            "Education:",
        )
    )
    check = readability(text)
    assert (check.sections, check.roles, check.bullets) == (3, 2, 2)
    assert check.ok


def test_readability_flags_unreadable_text() -> None:
    assert not readability("").ok
    assert not readability("one long paragraph without any structure").ok


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("My CV.pdf", "My CV.pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ("C:\\Users\\x\\cv final.PDF", "cv final.pdf"),
        ("<script>.pdf", "script.pdf"),
        ("...", "document.pdf"),
        ("a" * 300 + ".pdf", "a" * 116 + ".pdf"),
    ],
)
def test_filenames_are_sanitised(raw: str, clean: str) -> None:
    assert sanitise_filename(raw) == clean


# ------------------------------------------------------------------ PDFs


def test_pdf_text_and_pages() -> None:
    content = extract_text(make_pdf(pages=2), 20)
    assert content.pages == 2
    assert "Experience" in content.text


def test_pdf_over_the_page_limit_returns_no_text() -> None:
    content = extract_text(make_pdf(pages=3), 2)
    assert (content.pages, content.text) == (3, "")


def test_encrypted_and_broken_pdfs_are_refused() -> None:
    with pytest.raises(ValidationFailed):
        extract_text(encrypted_pdf(), 20)
    with pytest.raises(ValidationFailed):
        extract_text(b"%PDF-1.7\nnot really a pdf", 20)


# ------------------------------------------------------------------ weights


def test_weights_must_sum_to_100() -> None:
    assert check_scoring_preferences(ScoringPreferences()) == []
    bad = ScoringPreferences(weights=ScoreWeights(skills=60))
    assert [e.pointer for e in check_scoring_preferences(bad)] == ["/scoring_preferences/weights"]
    custom = ScoringPreferences(weights=ScoreWeights(skills=40, track_pay=30, location=20))
    assert check_scoring_preferences(custom) == []


def test_other_preference_checks() -> None:
    prefs = ScoringPreferences(
        work_modes=("remote", "remote", "beach"),
        preferred_locations=("",) + ("x",) * 10,
        perm_salary_aud=-1,
    )
    pointers = {e.pointer for e in check_scoring_preferences(prefs)}
    assert pointers == {
        "/scoring_preferences/work_modes",
        "/scoring_preferences/preferred_locations",
        "/scoring_preferences/pay_floors/perm_salary_aud",
    }


def test_default_weights_keep_the_prompt_byte_identical() -> None:
    assert assess_instructions() is ASSESS_INSTRUCTIONS
    assert assess_instructions(DEFAULTS.scoring_preferences.weights) == ASSESS_INSTRUCTIONS
    assert preferences_block(DEFAULTS.scoring_preferences) is None


def test_custom_weights_rewrite_the_scoring_line() -> None:
    weights = ScoreWeights(skills=40, track_pay=30, location=20, freshness=10)
    text = assess_instructions(weights, with_preferences=True)
    assert "skills 0-40" in text
    assert "track_pay 0-30" in text
    assert "location 0-20 (20 for remote" in text
    assert "11 if unclear" in text
    assert "scoring_preferences" in text


def _assessment(**breakdown: int) -> Assessment:
    return Assessment(
        track="contract",
        breakdown=Breakdown(**breakdown),
        relevant=True,
        reason="Fits.",
        work_mode="on_site",
    )


def test_breakdown_is_clamped_to_the_users_weights() -> None:
    now = datetime(2026, 11, 27, tzinfo=UTC)
    prefs = ScoringPreferences(
        weights=ScoreWeights(skills=30, track_pay=30, location=20, freshness=20)
    )
    values = scored_values(
        _assessment(skills=50, track_pay=40, location=20),
        now - timedelta(minutes=5),
        now,
        preferences=prefs,
    )
    breakdown = values["score_breakdown"]
    assert breakdown == {"skills": 30, "track_pay": 30, "location": 20, "freshness": 20}
    assert values["score"] == 100


def test_default_weights_keep_todays_maths() -> None:
    now = datetime(2026, 11, 27, tzinfo=UTC)
    values = scored_values(_assessment(skills=60, track_pay=20, location=10), None, now)
    assert values["score_breakdown"] == {
        "skills": 50,
        "track_pay": 20,
        "location": 10,
        "freshness": 4,
    }


def test_work_mode_outside_the_accepted_ones_scores_no_location() -> None:
    now = datetime(2026, 11, 27, tzinfo=UTC)
    prefs = ScoringPreferences(work_modes=("remote",))
    values = scored_values(_assessment(skills=40, location=15), None, now, preferences=prefs)
    breakdown = values["score_breakdown"]
    assert isinstance(breakdown, dict)
    assert breakdown["location"] == 0
    risks = values["risks"]
    assert isinstance(risks, list)
    assert any("on site" in str(risk) for risk in cast("list[object]", risks))


def test_preferences_block_holds_only_what_was_set() -> None:
    block = preferences_block(
        ScoringPreferences(perm_salary_aud=150_000, preferred_locations=("Melbourne",))
    )
    assert block is not None
    assert block.name == "scoring_preferences"
    assert "150000" in block.text
    assert "Melbourne" in block.text
    assert "contract" not in block.text


# ------------------------------------------------------------------ roles age filter

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
ME = Principal(user_id=uuid.uuid4(), email="me@example.test", role=AppRole.USER)


async def _roles_aged() -> tuple[RoleService, dict[str, uuid.UUID]]:
    clock = FakeClock(NOW)
    store = MemoryRoles()
    service = RoleService(store, FakeContentGuard(), clock, SequentialIds())
    ages = {
        "posted-0": (NOW, NOW),
        "posted-7": (NOW - timedelta(days=7), NOW),
        "posted-8": (NOW - timedelta(days=8), NOW),
        "posted-14": (NOW - timedelta(days=14), NOW),
        "posted-15": (NOW - timedelta(days=15), NOW),
        "found-20": (None, NOW - timedelta(days=20)),
        "found-1": (None, NOW - timedelta(days=1)),
    }
    ids: dict[str, uuid.UUID] = {}
    for title, (posted, found) in ages.items():
        clock.set(found)
        role = await service.create_role(ME, RoleDraft(title=title, posted_at=posted))
        ids[title] = role.id
    clock.set(NOW)
    return service, ids


async def _titles(service: RoleService, query: RoleQuery) -> set[str]:
    page = await service.list_roles(ME, query, None, 50)
    return {role.title for role in page.items}


async def test_max_age_is_inclusive_and_falls_back_to_found_at() -> None:
    service, _ = await _roles_aged()
    assert await _titles(service, RoleQuery(max_age_days=7)) == {
        "posted-0",
        "posted-7",
        "found-1",
    }
    assert await _titles(service, RoleQuery(max_age_days=0)) == {"posted-0"}


async def test_min_age_means_strictly_older() -> None:
    service, _ = await _roles_aged()
    assert await _titles(service, RoleQuery(min_age_days=14)) == {"posted-15", "found-20"}
    assert await _titles(service, RoleQuery(min_age_days=7, max_age_days=14)) == {
        "posted-8",
        "posted-14",
    }


async def test_min_age_above_max_is_refused() -> None:
    service, _ = await _roles_aged()
    with pytest.raises(ValidationFailed):
        await service.list_roles(ME, RoleQuery(min_age_days=10, max_age_days=5), None, 10)
