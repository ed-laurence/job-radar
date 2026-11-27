"""The owner's seeded extra skills (`seed/owner-settings.json`, the fictional Alex Example):
job-seeker's "Skill: what/where (year)" lines, about the CVs' employers, adding things the CVs
don't already say."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.jobs.seed_settings import owner_skills
from app.settings import REPO_ROOT

SEED = REPO_ROOT / "seed"
LINE = re.compile(r"[A-Za-z][^:]{1,40}: .+ \((19|20)\d{2}\)")
EMPLOYERS = (
    "Saltbush Digital Services",
    "Tasman Analytics Services",
    "Sapphire Learning",
    "Lumen Partners Institute",
    "Orbital Payments Co",
    "Ridgeline Control Systems",
)


def _cv_text() -> str:
    return "\n".join(
        json.dumps(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((SEED / "documents").glob("*-cv.json"))
    ).casefold()


def test_seeded_skills_follow_the_job_seeker_style() -> None:
    skills = owner_skills(SEED)
    assert skills is not None
    lines = skills.splitlines()
    assert 8 <= len(lines) <= 12
    assert all(LINE.fullmatch(line) for line in lines)


def test_seeded_skills_are_new_but_about_the_cv_roles() -> None:
    skills = owner_skills(SEED) or ""
    cv = _cv_text()
    for line in skills.splitlines():
        skill = line.split(":", 1)[0].casefold()
        word = re.compile(f"(?<![a-z]){re.escape(skill)}(?![a-z])")
        assert not word.search(cv), f"{skill} is already on a CV"
    assert sum(1 for employer in EMPLOYERS if employer in skills) >= 4


def test_absent_or_empty_file_means_no_skills(tmp_path: Path) -> None:
    assert owner_skills(tmp_path) is None
    (tmp_path / "owner-settings.json").write_text('{"profile": {"skills": ["  "]}}', "utf-8")
    assert owner_skills(tmp_path) is None
