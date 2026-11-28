"""Parsing git's diff output into the Publisher's change set: statuses, renames, binary counts,
added lines matched to files by position, and output that doesn't line up is refused."""

from __future__ import annotations

import pytest

from app.adapters.codehost.git_changes import ChangeParseError, change_set
from app.domain.ports.devboard import FileChange

NUL = "\0"
STATUS = NUL.join(["M", "a.py", "R087", "old.py", "new.py", "A", "img.png", "T", "link", ""])
NUMSTAT = NUL.join(["2\t1\ta.py", "1\t0\t", "old.py", "new.py", "-\t-\timg.png", "0\t0\tlink", ""])
PATCH = "\n".join(
    [
        "diff --git a/a.py b/a.py",
        "--- a/a.py",
        "+++ b/a.py",
        "@@ -1 +1,2 @@",
        "-x = 0",
        "+x = 1",
        "++++ looks like a header but is content",
        "diff --git a/old.py b/new.py",
        "similarity index 87%",
        "@@ -3,0 +4 @@",
        "+y = 2\r",
        "diff --git a/img.png b/img.png",
        "Binary files /dev/null and b/img.png differ",
        "diff --git a/link b/link",
        "old mode 100644",
        "new mode 120000",
        "",
    ]
)


def test_files_statuses_counts_and_added_lines() -> None:
    changes = change_set(STATUS, NUMSTAT, PATCH)
    assert changes.files == (
        FileChange("a.py", "M", 2, 1),
        FileChange("new.py", "R", 1, 0, old_path="old.py"),
        FileChange("img.png", "A", 0, 0),
        FileChange("link", "M", 0, 0),
    )
    assert changes.added_lines == (
        ("a.py", "x = 1"),
        ("a.py", "+++ looks like a header but is content"),
        ("new.py", "y = 2"),
    )


def test_an_empty_diff_is_an_empty_change_set() -> None:
    changes = change_set("", "", "")
    assert (changes.files, changes.added_lines) == ((), ())


@pytest.mark.parametrize(
    ("status", "numstat", "patch"),
    [
        pytest.param("M\0a.py\0", "1\t0\ta.py\0", "", id="sections-missing"),
        pytest.param("R100\0old.py\0", "", "", id="truncated-rename"),
        pytest.param("M\0", "", "", id="truncated-entry"),
        pytest.param("M\0a.py\0", "1\t0\t\0old.py\0", "diff --git a/a b/a\n", id="bad-numstat"),
    ],
)
def test_output_that_does_not_line_up_is_refused(status: str, numstat: str, patch: str) -> None:
    with pytest.raises(ChangeParseError):
        change_set(status, numstat, patch)
