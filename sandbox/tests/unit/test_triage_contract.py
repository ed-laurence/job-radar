"""The /out/triage.json contract the backend parses, checked before the file is written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import triage_contract as tc

TASK = "T-1"
DUP = "00000000-0000-4000-8000-0000000000d1"
OPEN = frozenset({DUP})


def _repo(tmp_path: Path) -> Path:
    for name in (
        "backend/src/app/api/board.py",
        "backend/tests/unit/devboard/test_board.py",
        "web/src/features/board/Card.tsx",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    return tmp_path


def _verification(**overrides: object) -> dict[str, object]:
    verification: dict[str, object] = {
        "status": "confirmed",
        "evidence": "Card.tsx renders no badge when the priority is set.",
        "code_refs": ["web/src/features/board/Card.tsx"],
        "duplicate_of": None,
    }
    verification.update(overrides)
    return verification


def _triage(**overrides: object) -> dict[str, object]:
    triage: dict[str, object] = {
        "task_id": TASK,
        "type": "bug",
        "size": "S",
        "risk": "low",
        "areas": ["web"],
        "priority": "P2",
        "severity": "minor",
        "impact": "Maintainers can't see a card's priority at a glance.",
        "route": "next",
        "route_reason": "Small fix; nothing urgent depends on it.",
        "needs_human_design": False,
        "out_of_scope": False,
        "out_of_scope_reason": None,
        "likely_paths": ["web/src/features/board/Card.tsx", "web/src/features/board/Badge.tsx"],
        "verification": _verification(),
    }
    triage.update(overrides)
    return triage


def _errors(raw: object, repo: Path | None = None, open_cards: frozenset[str] = OPEN) -> str:
    with pytest.raises(tc.TriageError) as info:
        tc.validate(raw, TASK, repo, open_cards)
    assert info.value.errors
    return " | ".join(info.value.errors)


def test_a_valid_triage_is_returned_in_the_contract_shape(tmp_path: Path) -> None:
    triage = tc.validate(_triage(), TASK, _repo(tmp_path), OPEN)
    assert triage == _triage()
    assert list(triage) == list(tc.TOP_KEYS)
    assert list(tc.as_dict(triage["verification"])) == list(tc.VERIFICATION_KEYS)


def test_optional_fields_are_filled_so_the_file_always_has_every_key() -> None:
    raw = _triage(verification={"status": "unclear", "evidence": "Could not tell."})
    for key in ("task_id", "out_of_scope_reason", "likely_paths"):
        del raw[key]
    triage = tc.validate(raw, TASK)
    assert triage["task_id"] == TASK
    assert triage["likely_paths"] == []
    assert triage["out_of_scope_reason"] is None
    assert tc.as_dict(triage["verification"]) == {
        "status": "unclear",
        "evidence": "Could not tell.",
        "code_refs": [],
        "duplicate_of": None,
    }


def test_there_is_no_acceptance_criteria_field() -> None:
    criteria = [{"test_id": "web/src/a.test.tsx::x", "expectation": "x"}]
    assert "acceptance_criteria" in _errors(_triage(acceptance_criteria=criteria))


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ("not an object", "object"),
        (_triage(extra=1), "unknown key"),
        (_triage(task_id="T-2"), "task_id"),
        (_triage(type="epic"), "type"),
        (_triage(size="XL"), "size"),
        (_triage(risk="extreme"), "risk"),
        (_triage(areas=["db"]), "areas"),
        (_triage(areas=["web", "web"]), "repeat"),
        (_triage(areas=[]), "area"),
        (_triage(priority="P4"), "priority"),
        (_triage(priority="p1"), "priority"),
        (_triage(severity="blocker"), "severity"),
        (_triage(route="soon"), "route"),
        (_triage(impact=""), "impact"),
        (_triage(impact="x" * 601), "impact"),
        (_triage(route_reason="x" * 401), "route_reason"),
        (_triage(needs_human_design="no"), "needs_human_design"),
        (_triage(out_of_scope="no"), "out_of_scope"),
        (_triage(out_of_scope=True), "out_of_scope_reason"),
        (_triage(out_of_scope_reason="Not ours."), "out_of_scope_reason"),
        (_triage(out_of_scope=True, out_of_scope_reason="x" * 1001), "out_of_scope_reason"),
        (_triage(likely_paths=[f"web/src/f{i}.ts" for i in range(31)]), "likely_paths"),
        (_triage(likely_paths=["/etc/passwd"]), "repo-relative"),
        (_triage(likely_paths=["web/**/*.ts"]), "glob"),
        (_triage(verification="confirmed"), "verification"),
        (_triage(verification=_verification(colour=1)), "unknown key"),
        (_triage(verification=_verification(status="fixed")), "status"),
        (_triage(verification=_verification(evidence="")), "evidence"),
        (_triage(verification=_verification(evidence="x" * 1001)), "evidence"),
        (_triage(verification=_verification(code_refs=["a"] * 21)), "code_refs"),
        (_triage(verification=_verification(code_refs=["../x"])), "repo-relative"),
    ],
)
def test_contract_violations_are_refused(raw: object, needle: str) -> None:
    assert needle in _errors(raw)


def test_an_out_of_scope_card_needs_no_area() -> None:
    raw = _triage(areas=[], out_of_scope=True, out_of_scope_reason="Infra work; not web or API.")
    assert tc.validate(raw, TASK)["out_of_scope"] is True


def test_code_refs_must_exist_and_likely_paths_need_an_existing_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    text = _errors(
        _triage(
            likely_paths=["web/src/nowhere/deep/x.tsx"],
            verification=_verification(code_refs=["web/src/features/board/Missing.tsx"]),
        ),
        repo,
    )
    assert "web/src/nowhere/deep/x.tsx" in text
    assert "web/src/features/board/Missing.tsx" in text


def test_code_refs_may_name_existing_directories(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _triage(verification=_verification(code_refs=["backend/src/app/api/", "web/src"]))
    assert tc.validate(raw, TASK, repo, OPEN)["type"] == "bug"


def test_a_duplicate_names_one_of_the_open_cards() -> None:
    dup = _verification(status="duplicate", duplicate_of=DUP)
    assert (
        tc.as_dict(tc.validate(_triage(verification=dup), TASK, None, OPEN)["verification"])[
            "duplicate_of"
        ]
        == DUP
    )
    other = _verification(status="duplicate", duplicate_of="00000000-0000-4000-8000-0000000000ff")
    assert "open cards" in _errors(_triage(verification=other))
    assert "duplicate_of" in _errors(_triage(verification=_verification(status="duplicate")))
    assert "duplicate" in _errors(_triage(verification=_verification(duplicate_of=DUP)))


def test_without_the_open_card_list_any_uuid_is_accepted_for_a_duplicate() -> None:
    """The backend re-reads the file without the card list; it checks the id itself."""
    dup = _verification(status="duplicate", duplicate_of=DUP)
    assert tc.validate(_triage(verification=dup), TASK)["type"] == "bug"
    bad = _verification(status="duplicate", duplicate_of="card-1")
    with pytest.raises(tc.TriageError):
        tc.validate(_triage(verification=bad), TASK)


def test_errors_are_capped_and_never_echo_huge_input() -> None:
    raw = _triage(likely_paths=["/" + "x" * 5000] * 30, impact="x" * 5000)
    with pytest.raises(tc.TriageError) as info:
        tc.validate(raw, TASK)
    assert len(info.value.errors) <= tc.MAX_ERRORS
    assert "x" * 400 not in str(info.value)


def test_write_is_atomic_json(tmp_path: Path) -> None:
    out = tmp_path / "triage.json"
    tc.write(tc.validate(_triage(), TASK), out)
    assert json.loads(out.read_text(encoding="utf-8")) == _triage()
    assert not (tmp_path / "triage.json.tmp").exists()
