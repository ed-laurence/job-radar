"""Paths an agent names must be in the repo: an existing file or directory, or a new file in a
directory that exists. Nothing invented (e.g. `web/src/components/devboard/MetricsPanel.tsx`
when the code is in `web/src/features/devboard/`)."""

from __future__ import annotations

import pytest

from app.domain.devboard.repo_paths import criterion_file, repo_listing, unknown_paths

FILES = frozenset(
    {
        "web/src/features/devboard/BoardMetrics.tsx",
        "web/src/features/devboard/BoardMetrics.test.tsx",
        "backend/src/app/domain/devboard/metrics.py",
        "backend/tests/unit/devboard/test_metrics.py",
        "backend/src/app/generated/models.py",
        "infra/main.tf",
        "README.md",
    }
)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("web/src/features/devboard/BoardMetrics.tsx", id="existing-file"),
        pytest.param("web/src/features/devboard/", id="existing-dir"),
        pytest.param("web/src/features/devboard", id="existing-dir-no-slash"),
        pytest.param("backend/tests/unit/devboard/test_new.py", id="new-file-in-existing-dir"),
        pytest.param("./backend/src/app/domain/devboard/metrics.py", id="leading-dot-slash"),
    ],
)
def test_real_paths_are_accepted(path: str) -> None:
    assert unknown_paths([path], FILES) == ()


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("web/src/components/devboard/MetricsPanel.test.tsx", id="invented-dir"),
        pytest.param("web/src/components/devboard/", id="invented-dir-slash"),
        pytest.param("backend/tests/api/test_metrics.py", id="invented-test-dir"),
        pytest.param("web/src/features/devboard/BoardMetrics.tsx/", id="file-as-dir"),
        pytest.param("../outside.py", id="escapes-repo"),
        pytest.param("", id="empty"),
    ],
)
def test_invented_paths_are_reported(path: str) -> None:
    assert unknown_paths([path], FILES) == (path,)


def test_each_unknown_path_is_reported_once_in_order() -> None:
    bad = ["web/src/x/A.tsx", "web/src/features/devboard/", "web/src/y/B.tsx", "web/src/x/A.tsx"]
    assert unknown_paths(bad, FILES) == ("web/src/x/A.tsx", "web/src/y/B.tsx")


def test_the_test_file_is_the_part_before_the_node_name() -> None:
    node = "backend/tests/unit/a/test_b.py::does x"
    assert criterion_file(node) == "backend/tests/unit/a/test_b.py"
    assert criterion_file("web/src/A.test.tsx::renders :: twice") == "web/src/A.test.tsx"


def test_the_listing_holds_only_writable_files_sorted() -> None:
    listing = repo_listing(FILES).splitlines()
    assert listing == sorted(listing)
    assert "web/src/features/devboard/BoardMetrics.tsx" in listing
    assert "backend/tests/unit/devboard/test_metrics.py" in listing
    # Out of scope or generated: not offered to the model.
    assert "infra/main.tf" not in listing
    assert "README.md" not in listing
    assert "backend/src/app/generated/models.py" not in listing
