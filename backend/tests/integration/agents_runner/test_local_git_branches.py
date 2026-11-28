"""LocalGitHost on published branches, against real git in a temp dir: the Publisher's change set
(`changes`), deleting a branch, bundling a branch head for review and rework runs, and the
human-review views (`LocalBranchViews.diff` / `patch`). The source repo stays untouched."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from app.adapters.agents.process import capture
from app.adapters.codehost.local_git import GitError, LocalBranchViews, LocalGitHost, branch_name
from app.domain.errors import NotFound
from tests.integration.agents_runner.test_local_git_host import RUN, TASK, Source

pytestmark = pytest.mark.integration

OTHER_RUN = uuid.UUID("cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee")


async def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    done = await capture(["git", *args], env=env, cwd=cwd, timeout_s=60)
    assert done.code == 0, f"git {args[0]} failed"
    return done.stdout.decode()


def _tree_digest(git_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(git_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(git_dir.rglob("*"))
        if path.is_file()
    }


async def _edits(root: Path, env: dict[str, str], input_bundle: Path, name: str) -> Path:
    """The sandbox's work: modify, add, delete and rename files on agent/<TASK>, then bundle."""
    work = root / f"work-{name}"
    await _git(root, env, "clone", "-q", "--no-checkout", str(input_bundle), str(work))
    await _git(work, env, "checkout", "-q", "-B", f"agent/{TASK}", "HEAD")
    (work / "app.py").write_text("print('v3')\nprint('agent')\n", encoding="utf-8")
    (work / "added.py").write_text("token = 1\nflag = 2\n", encoding="utf-8")
    await _git(work, env, "rm", "-q", "new.py")
    (work / "docs").mkdir()
    (work / "docs" / "readme.md").write_text("A long line for rename detection.\n" * 5, "utf-8")
    await _git(work, env, "add", "-A")
    await _git(work, env, "commit", "-q", "-m", "agent change")
    await _git(work, env, "mv", "docs/readme.md", "docs/guide.md")
    await _git(work, env, "commit", "-q", "-m", "rename")
    result = root / f"{name}.bundle"
    await _git(work, env, "bundle", "create", str(result), f"agent/{TASK}")
    return result


async def _published(tmp_path: Path) -> tuple[Source, LocalGitHost, str, str]:
    source = Source(tmp_path)
    await source.init()
    host = LocalGitHost(source.repo, tmp_path / "cache" / "agent-remote")
    snapshot = await host.snapshot(tmp_path / "in" / "input.bundle")
    result = await _edits(tmp_path, source.env, snapshot.bundle, "first")
    branch = branch_name(TASK, RUN)
    await host.publish(result, f"agent/{TASK}", branch, snapshot.base_sha)
    return source, host, branch, snapshot.base_sha


async def test_changes_list_every_file_with_counts_and_the_added_lines(tmp_path: Path) -> None:
    _, host, branch, base = await _published(tmp_path)
    changes = await host.changes(base, branch)
    by_path = {change.path: change for change in changes.files}
    assert by_path["app.py"].status == "M"
    assert (by_path["app.py"].added, by_path["app.py"].deleted) == (2, 1)
    assert by_path["added.py"].status == "A"
    assert by_path["new.py"].status == "D"
    assert (by_path["new.py"].added, by_path["new.py"].deleted) == (0, 1)
    assert by_path["docs/guide.md"].status == "A"  # created and renamed on the branch
    assert set(by_path) == {"app.py", "added.py", "new.py", "docs/guide.md"}
    assert ("app.py", "print('agent')") in changes.added_lines
    assert ("added.py", "token = 1") in changes.added_lines
    assert not any(path == "new.py" for path, _ in changes.added_lines)


async def test_changes_report_a_rename_with_its_old_path(tmp_path: Path) -> None:
    source = Source(tmp_path)
    await source.init()
    (source.repo / "old_name.py").write_text("value = 'stable content line'\n" * 6, "utf-8")
    host = LocalGitHost(source.repo, tmp_path / "remote")
    snapshot = await host.snapshot(tmp_path / "in.bundle")
    work = tmp_path / "work"
    await _git(
        tmp_path, source.env, "clone", "-q", "--no-checkout", str(snapshot.bundle), str(work)
    )
    await _git(work, source.env, "checkout", "-q", "-B", f"agent/{TASK}", "HEAD")
    await _git(work, source.env, "mv", "old_name.py", "new_name.py")
    await _git(work, source.env, "commit", "-q", "-m", "rename")
    bundle = tmp_path / "r.bundle"
    await _git(work, source.env, "bundle", "create", str(bundle), f"agent/{TASK}")
    branch = branch_name(TASK, RUN)
    await host.publish(bundle, f"agent/{TASK}", branch, snapshot.base_sha)
    changes = await host.changes(snapshot.base_sha, branch)
    renamed = [change for change in changes.files if change.status == "R"]
    assert len(renamed) == 1
    assert (renamed[0].path, renamed[0].old_path) == ("new_name.py", "old_name.py")


async def test_delete_branch_removes_only_that_branch(tmp_path: Path) -> None:
    source, host, branch, base = await _published(tmp_path)
    views = LocalBranchViews(host)
    await host.delete_branch(branch)
    remote = tmp_path / "cache" / "agent-remote"
    refs = await _git(remote, source.env, "--git-dir", str(remote), "for-each-ref")
    assert branch not in refs
    with pytest.raises(NotFound):
        await views.diff(base, branch)
    await host.delete_branch(branch)  # already gone: nothing to do


async def test_bundle_branch_starts_the_next_run_at_the_branch_head(tmp_path: Path) -> None:
    source, host, branch, base = await _published(tmp_path)
    digest = _tree_digest(source.repo / ".git")
    snapshot = await host.bundle_branch(branch, base, tmp_path / "rework" / "input.bundle")
    assert snapshot.base_sha == base
    clone = tmp_path / "rework-clone"
    await _git(tmp_path, source.env, "clone", "-q", str(snapshot.bundle), str(clone))
    assert (clone / "app.py").read_text(encoding="utf-8") == "print('v3')\nprint('agent')\n"
    await _git(clone, source.env, "merge-base", "--is-ancestor", base, "HEAD")
    stat = await _git(clone, source.env, "diff", "--name-only", f"{base}..HEAD")
    assert "added.py" in stat
    # A rework run's result publishes as a new branch from that head.
    await _git(clone, source.env, "checkout", "-q", "-B", f"agent/{TASK}", "HEAD")
    (clone / "added.py").write_text("token = 1\nflag = 3\n", encoding="utf-8")
    await _git(clone, source.env, "commit", "-q", "-am", "rework")
    result = tmp_path / "rework.bundle"
    await _git(clone, source.env, "bundle", "create", str(result), f"agent/{TASK}")
    rework = branch_name(TASK, OTHER_RUN)
    await host.publish(result, f"agent/{TASK}", rework, base)
    changes = await host.changes(base, rework)
    assert ("added.py", "flag = 3") in changes.added_lines
    assert any(change.path == "app.py" for change in changes.files)  # still the whole diff
    assert _tree_digest(source.repo / ".git") == digest


async def test_bundle_branch_refuses_a_base_the_branch_does_not_descend_from(
    tmp_path: Path,
) -> None:
    _, host, branch, _ = await _published(tmp_path)
    with pytest.raises(GitError):
        await host.bundle_branch(branch, "0" * 40, tmp_path / "x.bundle")
    with pytest.raises(ValueError, match="branch"):
        await host.bundle_branch("main", "0" * 40, tmp_path / "x.bundle")


async def test_branch_views_diff_and_patch(tmp_path: Path) -> None:
    _, host, branch, base = await _published(tmp_path)
    views = LocalBranchViews(host)
    diff = await views.diff(base, branch)
    assert (diff.branch, diff.base_sha) == (branch, base)
    assert diff.files_changed == 4
    assert diff.insertions >= 4
    assert "+print('agent')" in diff.text
    patch = await views.patch(base, branch)
    assert patch.count("Subject: [PATCH") == 2
    assert "+print('agent')" in patch


async def test_branch_views_raise_not_found_for_a_missing_branch(tmp_path: Path) -> None:
    host = LocalGitHost(tmp_path / "project", tmp_path / "remote")
    views = LocalBranchViews(host)
    with pytest.raises(NotFound):
        await views.diff("a" * 40, "agent/nothing-here")
    with pytest.raises(NotFound):
        await views.patch("a" * 40, "agent/nothing-here")


# Regression: a failing remote git call named the scratch remote's host path as the "verb"
# (`git C:\...\agent-remote failed`), putting a local path into run outcomes.
async def test_git_errors_name_the_verb_not_the_remote_path(tmp_path: Path) -> None:
    _, host, branch, _ = await _published(tmp_path)
    with pytest.raises(GitError) as raised:
        await host.changes("f" * 40, branch)
    assert str(raised.value) == "git diff failed (exit 128)"
