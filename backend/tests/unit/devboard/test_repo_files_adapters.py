"""`RepoFiles` adapters: the working tree without `.gitignore`d files (read through a throwaway
GIT_DIR, never the project's `.git`), and a frozen listing for offline replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.codehost.working_tree import ListedRepoFiles, WorkingTreeFiles, parse_listing
from app.domain.devboard.repo_paths import repo_listing
from app.domain.errors import UpstreamUnavailable
from app.settings import Settings


def _tree(root: Path) -> None:
    for name in ("web/src/features/a/A.tsx", "backend/tests/unit/test_a.py", "build/out.js"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
    (root / "debug.log").write_text("x", encoding="utf-8")


async def test_the_working_tree_leaves_ignored_files_out(tmp_path: Path) -> None:
    _tree(tmp_path)
    files = await WorkingTreeFiles(tmp_path).paths()
    assert files == {".gitignore", "web/src/features/a/A.tsx", "backend/tests/unit/test_a.py"}


async def test_listing_a_folder_creates_no_git_dir_in_it(tmp_path: Path) -> None:
    _tree(tmp_path)
    await WorkingTreeFiles(tmp_path).paths()
    assert not (tmp_path / ".git").exists()


async def test_a_missing_git_is_upstream_unavailable(tmp_path: Path) -> None:
    with pytest.raises(UpstreamUnavailable) as caught:
        await WorkingTreeFiles(tmp_path, git="no-such-git-binary").paths()
    assert caught.value.detail == "git is not installed"


async def test_a_failing_git_step_names_only_the_verb_and_exit_code(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(UpstreamUnavailable) as caught:
        await WorkingTreeFiles(missing).paths()
    assert caught.value.detail is not None
    assert caught.value.detail.startswith("git ")
    assert "(exit " in caught.value.detail
    assert str(missing) not in caught.value.detail


async def test_a_frozen_listing_is_read_line_by_line(tmp_path: Path) -> None:
    listing = tmp_path / "repo_files.txt"
    text = "web/src/a.ts\n\n  backend/tests/unit/test_b.py \nweb/src/\n"
    listing.write_text(text, encoding="utf-8")
    expected = {"web/src/a.ts", "backend/tests/unit/test_b.py"}
    assert await ListedRepoFiles(listing).paths() == expected


async def test_a_missing_listing_is_upstream_unavailable(tmp_path: Path) -> None:
    with pytest.raises(UpstreamUnavailable):
        await ListedRepoFiles(tmp_path / "none.txt").paths()


def test_nul_separated_output_and_backslashes_are_normalised() -> None:
    assert parse_listing("a\\b.py\0c/d.ts\0") == {"a/b.py", "c/d.ts"}


async def test_the_frozen_replay_listing_holds_only_writable_files() -> None:
    listing = Settings().llm_fixtures_dir / "devboard" / "repo_files.txt"
    files = await ListedRepoFiles(listing).paths()
    assert "web/src/features/devboard/Board.tsx" in files
    assert repo_listing(files).splitlines() == sorted(files)
