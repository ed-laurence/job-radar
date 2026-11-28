"""GitHubHost against real git, with a local bare repo standing in for GitHub (no network).

Snapshot comes from the "GitHub" `main` (never the project's working tree); the publish commit
carries the exact trailers agent-pr.yml reads; the push creates `agent/task-<id>`; a rework
push fast-forwards; a diverged remote branch fails the push without forcing."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

from app.adapters.agents.process import capture
from app.adapters.codehost.github import GitHubAccess, GitHubHost, PublishTrailers
from app.adapters.codehost.local_git import GitError, branch_name
from app.domain.ports.devboard import github_branch

pytestmark = pytest.mark.integration

TASK = uuid.UUID("11111111-2222-3333-4444-555555555555")
RUN_1 = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
RUN_2 = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
# agent-pr.yml's checks on the pushed head.
WORKFLOW_TASK = re.compile(r"^task-[0-9a-f-]{8,36}$")
WORKFLOW_TESTS = re.compile(r"^[0-9]{1,5}/[0-9]{1,5}$")
WORKFLOW_LINES = re.compile(r"^[0-9]{1,5}$")


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


class World:
    """A fake GitHub (bare repo with `main`), a project with uncommitted work, a scratch remote."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.env = _env(self.home)
        self.github = root / "github.git"
        self.project = root / "project"
        self.project.mkdir()
        self.remote = root / "cache" / "agent-remote"
        keys = root / "keys"
        keys.mkdir()
        self.key = keys / "github_deploy_key"
        self.key.write_text("not-a-real-key\n", encoding="utf-8")
        self.known_hosts = keys / "github_known_hosts"
        self.known_hosts.write_text("github.com ssh-ed25519 AAAA-test-only\n", encoding="utf-8")

    async def init(self) -> str:
        await _git(self.root, self.env, "init", "-q", "--bare", "-b", "main", str(self.github))
        seed = self.root / "seed"
        await _git(self.root, self.env, "init", "-q", "-b", "main", str(seed))
        (seed / "app.py").write_text("print('github main')\n", encoding="utf-8")
        await _git(seed, self.env, "add", "-A")
        await _git(seed, self.env, "commit", "-q", "-m", "main on github")
        await _git(seed, self.env, "push", "-q", str(self.github), "main:refs/heads/main")
        # The project's working tree differs from GitHub main: it must never reach the agent.
        (self.project / "app.py").write_text("print('uncommitted local')\n", encoding="utf-8")
        (self.project / "local-only.py").write_text("x = 1\n", encoding="utf-8")
        return (await _git(seed, self.env, "rev-parse", "HEAD")).strip()

    def host(self, url: str | None = None) -> GitHubHost:
        access = GitHubAccess(
            url=url or str(self.github), deploy_key=self.key, known_hosts=self.known_hosts
        )
        return GitHubHost(self.project, self.remote, access)

    async def github_git(self, *args: str) -> str:
        return await _git(self.github, self.env, "--git-dir", str(self.github), *args)


async def _agent_edits(root: Path, env: dict[str, str], bundle: Path, text: str) -> Path:
    """What the sandbox does: clone the input bundle, commit on agent/<TASK>, bundle it."""
    work = root / f"sandbox-{uuid.uuid4().hex[:8]}"
    await _git(root, env, "clone", "-q", "--no-checkout", str(bundle), str(work))
    await _git(work, env, "checkout", "-q", "-B", f"agent/{TASK}", "HEAD")
    (work / "app.py").write_text(text, encoding="utf-8")
    await _git(work, env, "commit", "-q", "-am", "agent change")
    result = root / f"result-{uuid.uuid4().hex[:8]}.bundle"
    await _git(work, env, "bundle", "create", "-q", str(result), f"agent/{TASK}")
    return result


def _trailers(passed: int = 7, failed: int = 1, lines: int = 3) -> PublishTrailers:
    return PublishTrailers(task_id=TASK, passed=passed, failed=failed, diff_lines=lines)


async def _workflow_trailer(world: World, ref: str, key: str) -> str:
    fmt = f"--format=%(trailers:key={key},valueonly,only)"
    out = await world.github_git("log", "-1", fmt, ref)
    return out.splitlines()[0].strip() if out.strip() else ""


async def test_snapshot_comes_from_github_main_not_the_working_tree(tmp_path: Path) -> None:
    world = World(tmp_path)
    main_sha = await world.init()
    snapshot = await world.host().snapshot(tmp_path / "in" / "input.bundle")
    assert snapshot.base_sha == main_sha
    clone = tmp_path / "check"
    await _git(tmp_path, world.env, "clone", "-q", str(snapshot.bundle), str(clone))
    assert (clone / "app.py").read_text(encoding="utf-8") == "print('github main')\n"
    assert not (clone / "local-only.py").exists()
    tracking = await _git(
        world.remote, world.env, "--git-dir", str(world.remote), "rev-parse", "github/main"
    )
    assert tracking.strip() == main_sha


async def test_publish_commit_has_the_trailers_and_push_creates_the_card_branch(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    await world.init()
    host = world.host()
    snapshot = await host.snapshot(tmp_path / "in.bundle")
    result = await _agent_edits(tmp_path, world.env, snapshot.bundle, "print('agent')\n")
    scratch = branch_name(TASK, RUN_1)
    published = await host.publish(result, f"agent/{TASK}", scratch, snapshot.base_sha)

    pushed = await host.push(scratch, _trailers())

    target = github_branch(TASK)
    assert pushed.branch == target == f"agent/task-{TASK}"
    remote_head = (await world.github_git("rev-parse", f"refs/heads/{target}")).strip()
    assert remote_head == pushed.sha
    # One commit on top of the agent's head, with the same tree.
    parent = (await world.github_git("rev-parse", f"{remote_head}^")).strip()
    assert parent == published.head_sha
    trees = await world.github_git("rev-parse", f"{remote_head}^{{tree}}", f"{parent}^{{tree}}")
    first, second = trees.split()
    assert first == second
    # The fixed publisher identity and message.
    who = await world.github_git("log", "-1", "--format=%an <%ae>|%cn <%ce>|%s", remote_head)
    assert who.strip() == (
        f"jobradar-publisher <publisher@example.test>|"
        f"jobradar-publisher <publisher@example.test>|jobradar: publish task-{TASK}"
    )
    # agent-pr.yml's own trailer reads and checks.
    task = await _workflow_trailer(world, remote_head, "Jobradar-Task")
    assert WORKFLOW_TASK.match(task)
    assert target == f"agent/{task}"
    assert await _workflow_trailer(world, remote_head, "Jobradar-Verdict") == "pass"
    tests = await _workflow_trailer(world, remote_head, "Jobradar-Tests")
    assert tests == "7/8"
    assert WORKFLOW_TESTS.match(tests)
    lines = await _workflow_trailer(world, remote_head, "Jobradar-Diff-Lines")
    assert lines == "3"
    assert WORKFLOW_LINES.match(lines)
    # The scratch branch now ends with the publish commit, so a rework starts from it.
    scratch_head = await _git(
        world.remote, world.env, "--git-dir", str(world.remote), "rev-parse", scratch
    )
    assert scratch_head.strip() == pushed.sha
    # GitHub main is untouched.
    assert (await world.github_git("rev-parse", "main")).strip() == snapshot.base_sha


async def test_a_rework_push_fast_forwards_the_same_branch(tmp_path: Path) -> None:
    world = World(tmp_path)
    await world.init()
    host = world.host()
    snapshot = await host.snapshot(tmp_path / "in.bundle")
    first = await _agent_edits(tmp_path, world.env, snapshot.bundle, "print('one')\n")
    scratch_1 = branch_name(TASK, RUN_1)
    await host.publish(first, f"agent/{TASK}", scratch_1, snapshot.base_sha)
    pushed_1 = await host.push(scratch_1, _trailers())

    # Rework: the input is the card's scratch branch head (as LocalGitHost does today).
    rework_in = await host.bundle_branch(scratch_1, snapshot.base_sha, tmp_path / "rw.bundle")
    second = await _agent_edits(tmp_path, world.env, rework_in.bundle, "print('two')\n")
    scratch_2 = branch_name(TASK, RUN_2)
    await host.publish(second, f"agent/{TASK}", scratch_2, snapshot.base_sha)
    pushed_2 = await host.push(scratch_2, _trailers(passed=8, failed=0, lines=4))

    head = (await world.github_git("rev-parse", f"refs/heads/{github_branch(TASK)}")).strip()
    assert head == pushed_2.sha != pushed_1.sha
    await world.github_git("merge-base", "--is-ancestor", pushed_1.sha, head)
    assert await _workflow_trailer(world, head, "Jobradar-Tests") == "8/8"


async def test_a_diverged_remote_branch_fails_the_push_without_forcing(tmp_path: Path) -> None:
    world = World(tmp_path)
    await world.init()
    host = world.host()
    snapshot = await host.snapshot(tmp_path / "in.bundle")
    first = await _agent_edits(tmp_path, world.env, snapshot.bundle, "print('one')\n")
    scratch_1 = branch_name(TASK, RUN_1)
    await host.publish(first, f"agent/{TASK}", scratch_1, snapshot.base_sha)
    await host.push(scratch_1, _trailers())
    # Someone else moves the GitHub branch.
    other = tmp_path / "other"
    target = github_branch(TASK)
    await _git(tmp_path, world.env, "clone", "-q", "-b", target, str(world.github), str(other))
    (other / "app.py").write_text("print('someone else')\n", encoding="utf-8")
    await _git(other, world.env, "commit", "-q", "-am", "diverge")
    await _git(other, world.env, "push", "-q", "origin", target)
    moved = (await world.github_git("rev-parse", f"refs/heads/{target}")).strip()

    # A fresh (non-rework) run from main: its push is not a fast-forward.
    fresh_in = await host.snapshot(tmp_path / "fresh.bundle")
    second = await _agent_edits(tmp_path, world.env, fresh_in.bundle, "print('fresh')\n")
    scratch_2 = branch_name(TASK, RUN_2)
    await host.publish(second, f"agent/{TASK}", scratch_2, fresh_in.base_sha)
    with pytest.raises(GitError) as raised:
        await host.push(scratch_2, _trailers())

    assert str(raised.value) == "git push failed (exit 1)"
    assert (await world.github_git("rev-parse", f"refs/heads/{target}")).strip() == moved
    # The scratch branch stays, so the card can be retried.
    assert await host.has_branch(scratch_2)


async def test_errors_carry_only_the_verb_and_exit_code(tmp_path: Path) -> None:
    world = World(tmp_path)
    await world.init()
    host = world.host(url=str(tmp_path / "no-such-repo.git"))
    with pytest.raises(GitError) as raised:
        await host.snapshot(tmp_path / "in.bundle")
    message = str(raised.value)
    assert re.fullmatch(r"git fetch failed \(exit \d+\)", message)
    assert str(world.key) not in message
    assert "no-such-repo" not in message


async def test_a_missing_deploy_key_fails_before_any_git_call(tmp_path: Path) -> None:
    world = World(tmp_path)
    await world.init()
    world.key.unlink()
    with pytest.raises(GitError, match=r"^The GitHub deploy key or known_hosts file is missing\.$"):
        await world.host().snapshot(tmp_path / "in.bundle")
    assert not (world.remote / "refs" / "remotes").exists()


async def test_push_only_takes_agent_branches(tmp_path: Path) -> None:
    world = World(tmp_path)
    with pytest.raises(ValueError, match="branch"):
        await world.host().push("main", _trailers())
