"""LocalGitHost against real git: a temp source repo and a temp bare remote. The source repo's
status, refs and every byte under its `.git` are unchanged by snapshot, publish and diff.
Also the real process runner's stop path, with the Python interpreter standing in for docker."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

from app.adapters.agents.process import AsyncioProcessRunner, capture
from app.adapters.codehost.local_git import GitError, LocalGitHost, branch_name

pytestmark = pytest.mark.integration

TASK = uuid.UUID("11111111-2222-3333-4444-555555555555")
RUN = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _env(home: Path) -> dict[str, str]:
    config = home / "gitconfig"
    if not config.exists():
        config.write_text("", encoding="utf-8")
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATHEXT")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(config),
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.test",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.test",
        HOME=str(home),
    )
    return env


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


class Source:
    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.home.mkdir()
        self.env = _env(self.home)
        self.repo = root / "project"
        self.repo.mkdir()

    async def init(self) -> None:
        await _git(self.repo, self.env, "init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text("*.env\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
        await _git(self.repo, self.env, "add", "-A")
        await _git(self.repo, self.env, "commit", "-q", "-m", "base")
        # Uncommitted work and an ignored secret: the snapshot is of the working tree.
        (self.repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "local.env").write_text("TOKEN=not-a-real-secret\n", encoding="utf-8")

    async def state(self) -> tuple[str, str, str]:
        status = await _git(self.repo, self.env, "status", "--porcelain", "--untracked-files=all")
        refs = await _git(self.repo, self.env, "for-each-ref", "--format=%(refname) %(objectname)")
        head = await _git(self.repo, self.env, "rev-parse", "HEAD")
        return status, refs, head


async def _agent_edits(root: Path, env: dict[str, str], input_bundle: Path) -> Path:
    """What the sandbox does: clone the input bundle, commit on agent/<TASK_ID>, bundle it."""
    work = root / "sandbox-work"
    await _git(root, env, "clone", "-q", "--no-checkout", str(input_bundle), str(work))
    await _git(work, env, "checkout", "-q", "-B", f"agent/{TASK}", "HEAD")
    (work / "app.py").write_text("print('v3')\nprint('agent')\n", encoding="utf-8")
    await _git(work, env, "commit", "-q", "-am", "agent change")
    result = root / "result.bundle"
    await _git(work, env, "bundle", "create", str(result), f"agent/{TASK}")
    return result


# Regression: on Windows git records no exec bit, so the snapshot stored scripts/gen-api.sh as
# 100644 and the sandbox writer got "Permission denied" running it. Scripts (a `#!` first line)
# are executable in the snapshot; other files are not.
async def test_snapshot_keeps_scripts_executable(tmp_path: Path) -> None:
    source = Source(tmp_path)
    await source.init()
    (source.repo / "scripts").mkdir()
    (source.repo / "scripts" / "gen.sh").write_text("#!/usr/bin/env bash\necho hi\n", "utf-8")
    (source.repo / "notes.txt").write_text("#not a script\n", encoding="utf-8")
    host = LocalGitHost(source.repo, tmp_path / "cache" / "agent-remote")
    snapshot = await host.snapshot(tmp_path / "in" / "input.bundle")
    clone = tmp_path / "check"
    await _git(tmp_path, source.env, "clone", "-q", str(snapshot.bundle), str(clone))
    modes = await _git(clone, source.env, "ls-tree", "-r", "HEAD")
    by_path = {line.split("\t")[1]: line.split()[0] for line in modes.splitlines()}
    assert by_path["scripts/gen.sh"] == "100755"
    assert by_path["notes.txt"] == "100644"
    assert by_path["app.py"] == "100644"


async def test_snapshot_publish_and_diff_leave_the_source_repo_untouched(tmp_path: Path) -> None:
    source = Source(tmp_path)
    await source.init()
    before = await source.state()
    digest_before = _tree_digest(source.repo / ".git")

    remote = tmp_path / "cache" / "agent-remote"
    host = LocalGitHost(source.repo, remote)
    snapshot = await host.snapshot(tmp_path / "in" / "input.bundle")

    clone = tmp_path / "check"
    await _git(tmp_path, source.env, "clone", "-q", str(snapshot.bundle), str(clone))
    assert (clone / "app.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert (clone / "new.py").is_file()
    assert not (clone / "local.env").exists(), "ignored files must not enter the snapshot"
    assert (await _git(clone, source.env, "rev-parse", "HEAD")).strip() == snapshot.base_sha

    result = await _agent_edits(tmp_path, source.env, snapshot.bundle)
    branch = branch_name(TASK, RUN)
    assert branch == "agent/11111111-aaaaaaaa"
    published = await host.publish(result, f"agent/{TASK}", branch, snapshot.base_sha)

    assert published.branch == branch
    assert published.base_sha == snapshot.base_sha
    assert published.stat.files_changed == 1
    assert published.stat.insertions == 2
    assert published.stat.deletions == 1
    assert "app.py" in published.stat.text
    refs = await _git(remote, source.env, "--git-dir", str(remote), "for-each-ref")
    assert f"refs/heads/{branch}" in refs
    assert published.head_sha in refs

    diff = await host.diff(snapshot.base_sha, branch)
    assert "+print('agent')" in diff
    assert (await host.diff_stat(snapshot.base_sha, branch)) == published.stat

    assert _tree_digest(source.repo / ".git") == digest_before
    assert await source.state() == before


async def test_snapshot_works_before_the_first_commit(tmp_path: Path) -> None:
    source = Source(tmp_path)
    await _git(source.repo, source.env, "init", "-q", "-b", "main")
    (source.repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    digest_before = _tree_digest(source.repo / ".git")
    snapshot = await LocalGitHost(source.repo, tmp_path / "remote").snapshot(tmp_path / "s.bundle")
    assert len(snapshot.base_sha) == 40
    assert _tree_digest(source.repo / ".git") == digest_before


async def test_publish_refuses_a_ref_that_is_not_in_the_bundle(tmp_path: Path) -> None:
    source = Source(tmp_path)
    await source.init()
    host = LocalGitHost(source.repo, tmp_path / "remote")
    snapshot = await host.snapshot(tmp_path / "in.bundle")
    result = await _agent_edits(tmp_path, source.env, snapshot.bundle)
    with pytest.raises(GitError):
        await host.publish(result, "agent/someone-else", branch_name(TASK, RUN), snapshot.base_sha)


@pytest.mark.parametrize("branch", ["main", "agent/../x", "agent/a b", "refs/heads/agent/x"])
async def test_publish_only_writes_agent_branches(tmp_path: Path, branch: str) -> None:
    host = LocalGitHost(tmp_path / "project", tmp_path / "remote")
    with pytest.raises(ValueError, match="branch"):
        await host.publish(tmp_path / "r.bundle", f"agent/{TASK}", branch, "a" * 40)


def test_remote_inside_the_project_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        LocalGitHost(tmp_path, tmp_path / "remote")


async def test_process_runner_stops_with_the_kill_command(tmp_path: Path) -> None:
    marker = tmp_path / "killed"
    runner = AsyncioProcessRunner(poll_s=0.05, grace_s=0.5)
    stop = threading.Event()
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    killer = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    job = asyncio.ensure_future(runner.run(sleeper, dict(os.environ), stop=stop, kill_argv=killer))
    await asyncio.sleep(0.3)
    stop.set()
    code = await asyncio.wait_for(job, 10)
    assert code != 0
    assert marker.exists()


async def test_process_runner_returns_the_exit_code() -> None:
    runner = AsyncioProcessRunner(poll_s=0.05, grace_s=0.5)
    argv = [sys.executable, "-c", "raise SystemExit(3)"]
    code = await runner.run(argv, dict(os.environ), stop=threading.Event(), kill_argv=["x"])
    assert code == 3
