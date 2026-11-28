"""The Publisher's pure rules (docs/features/agent-review.md §1): one test per rule, plus a clean
branch that passes. Each violation reads "<RULE>: path — reason"."""

from __future__ import annotations

import pytest

from app.domain.devboard.paths import CONTRACT, REGENERATED
from app.domain.devboard.publisher import (
    MAX_CHANGED_LINES,
    PRAGMAS,
    DevRunSummary,
    dev_run_outcome,
    parse_dev_run_outcome,
    violations,
)
from app.domain.ports.devboard import ChangeSet, FileChange

LEASES = ("web/src/features/board/", "backend/tests/unit/devboard/test_board.py")
FEATURE = "web/src/features/board/Card.tsx"
NEW_TEST = "web/src/features/board/Card.test.tsx"
OLD_TEST = "backend/tests/unit/devboard/test_board.py"


def _changes(*files: FileChange, added: tuple[tuple[str, str], ...] = ()) -> ChangeSet:
    return ChangeSet(files=files, added_lines=added)


def _rules(found: tuple[str, ...]) -> list[str]:
    return [message.split(":", 1)[0] for message in found]


def test_a_clean_branch_has_no_violations() -> None:
    changes = _changes(
        FileChange(FEATURE, "M", 10, 2),
        FileChange(NEW_TEST, "A", 30, 0),
        added=((FEATURE, "export const badge = 1;"), (NEW_TEST, "it('shows the badge', ...)")),
    )
    assert violations(changes, leases=LEASES, labels=()) == ()


def test_pub_lease_refuses_a_path_outside_the_leases() -> None:
    outside = "web/src/features/settings/Page.tsx"
    found = violations(_changes(FileChange(outside, "M", 1, 1)), leases=LEASES, labels=())
    assert found == (f"PUB-LEASE: {outside} — outside the run's leases",)


def test_pub_lease_checks_the_old_path_of_a_rename() -> None:
    renamed = FileChange(FEATURE, "R", 0, 0, old_path="web/src/features/other/Card.tsx")
    found = violations(_changes(renamed), leases=LEASES, labels=())
    assert _rules(found) == ["PUB-LEASE"]
    assert "web/src/features/other/Card.tsx" in found[0]


def test_pub_scope_refuses_a_path_agents_may_not_write() -> None:
    protected = "backend/src/app/api/security.py"
    found = violations(
        _changes(FileChange(protected, "M", 1, 0)), leases=("backend/src/app/api/",), labels=()
    )
    assert found == (
        f"PUB-LEASE: {protected} — not leasable",
        f"PUB-SCOPE: {protected} — not writable by agents",
    )


def test_pub_scope_and_lease_both_flag_a_path_outside_the_allowlist() -> None:
    found = violations(_changes(FileChange("AGENTS.md", "M", 1, 0)), leases=LEASES, labels=())
    assert _rules(found) == ["PUB-LEASE", "PUB-SCOPE"]


def test_pub_scope_allows_regenerated_code_only_with_the_contract() -> None:
    leases = ("api/openapi.yaml", "backend/src/app/generated/")
    generated = FileChange("backend/src/app/generated/models.py", "M", 50, 10)
    alone = violations(_changes(generated), leases=leases, labels=())
    assert alone == (
        "PUB-SCOPE: backend/src/app/generated/models.py — regenerated code changed without "
        "api/openapi.yaml",
    )
    contract = FileChange("api/openapi.yaml", "M", 5, 0)
    assert violations(_changes(contract, generated), leases=leases, labels=()) == ()


@pytest.mark.parametrize("status", ["M", "D"])
def test_pub_tests_refuses_changing_an_existing_test_without_a_label(status: str) -> None:
    found = violations(_changes(FileChange(OLD_TEST, status, 1, 1)), leases=LEASES, labels=())
    assert found == (f"PUB-TESTS: {OLD_TEST} — existing test changed without a modify-tests label",)


def test_pub_tests_accepts_a_covering_modify_tests_label() -> None:
    labels = ("modify-tests:backend/tests/unit/devboard",)
    found = violations(_changes(FileChange(OLD_TEST, "M", 1, 1)), leases=LEASES, labels=labels)
    assert found == ()


def test_pub_tests_checks_a_renamed_test_by_its_old_path() -> None:
    renamed = FileChange("web/src/features/board/Moved.test.tsx", "R", 0, 0, old_path=NEW_TEST)
    found = violations(_changes(renamed), leases=LEASES, labels=())
    assert found == (f"PUB-TESTS: {NEW_TEST} — existing test changed without a modify-tests label",)


def test_pub_tests_never_allows_test_infrastructure() -> None:
    conftest = "backend/tests/unit/devboard/conftest.py"
    labels = ("modify-tests:backend/tests/unit/devboard",)
    found = violations(
        _changes(FileChange(conftest, "A", 3, 0)),
        leases=("backend/tests/unit/devboard/",),
        labels=labels,
    )
    assert f"PUB-TESTS: {conftest} — test infrastructure is never changed" in found


@pytest.mark.parametrize("pragma", PRAGMAS)
def test_pub_pragma_refuses_every_skip_or_ignore_pragma(pragma: str) -> None:
    changes = _changes(FileChange(FEATURE, "M", 1, 0), added=((FEATURE, f"x = 1  # {pragma}"),))
    found = violations(changes, leases=LEASES, labels=())
    assert found == (f"PUB-PRAGMA: {FEATURE} — adds `{pragma}`",)


def test_pub_pragma_accepts_an_allow_label_for_that_pragma_only() -> None:
    added = ((FEATURE, "a = 1  # noqa"), (FEATURE, "b = 2  # type: ignore"))
    changes = _changes(FileChange(FEATURE, "M", 2, 0), added=added)
    found = violations(changes, leases=LEASES, labels=("allow:noqa",))
    assert found == (f"PUB-PRAGMA: {FEATURE} — adds `type: ignore`",)


def test_pub_pragma_reports_each_pragma_once_per_file() -> None:
    added = ((FEATURE, "a  # noqa"), (FEATURE, "b  # noqa"))
    changes = _changes(FileChange(FEATURE, "M", 2, 0), added=added)
    found = violations(changes, leases=LEASES, labels=())
    assert found == (f"PUB-PRAGMA: {FEATURE} — adds `noqa`",)


def test_pub_size_caps_changed_lines_but_not_regenerated_code() -> None:
    leases = (*LEASES, CONTRACT, *REGENERATED)
    big = FileChange(FEATURE, "M", MAX_CHANGED_LINES - 10, 10)
    generated = FileChange("web/src/lib/api/generated/schema.d.ts", "M", 5000, 4000)
    contract = FileChange("api/openapi.yaml", "M", 0, 0)
    assert violations(_changes(big, generated, contract), leases=leases, labels=()) == ()
    over = FileChange(NEW_TEST, "A", 1, 0)
    found = violations(_changes(big, over), leases=leases, labels=())
    assert found == (
        f"PUB-SIZE: branch — {MAX_CHANGED_LINES + 1} changed lines, over the "
        f"{MAX_CHANGED_LINES}-line cap (regenerated code not counted)",
    )


# ---------------------------------------------------------------- the dev run's outcome
def test_the_dev_run_outcome_round_trips_its_counts() -> None:
    summary = DevRunSummary(
        branch="agent/11111111-aaaaaaaa",
        files_changed=2,
        insertions=10,
        deletions=3,
        passed=5,
        failed=1,
        first_failing="web/src/a.test.tsx::Card > shows (empty) state",
    )
    text = dev_run_outcome(summary, "done")
    assert text == (
        "Branch agent/11111111-aaaaaaaa: 2 files changed, +10 -3; handoff done; tests 5 passed,"
        " 1 failed, first failing `web/src/a.test.tsx::Card > shows (empty) state`."
    )
    assert parse_dev_run_outcome(text + " Model spend unknown.") == summary


def test_a_first_failing_id_with_backticks_or_control_characters_is_left_out() -> None:
    for raw in ("a`b", "a\nb", "x" * 161):
        summary = DevRunSummary("agent/a-b", 1, 1, 0, 0, 1, first_failing=raw)
        parsed = parse_dev_run_outcome(dev_run_outcome(summary, "failed"))
        assert parsed is not None
        assert parsed.first_failing is None


def test_other_outcomes_do_not_parse() -> None:
    assert parse_dev_run_outcome(None) is None
    assert parse_dev_run_outcome("Stopped.") is None
    assert parse_dev_run_outcome("The Publisher refused the branch: PUB-SIZE: branch — x.") is None
