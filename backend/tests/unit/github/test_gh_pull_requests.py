"""`GhPullRequests`: one read-only `gh pr list` per lookup, strict parsing of its JSON, and gh
failures as `UpstreamUnavailable` with a safe message. gh itself is faked: no GitHub calls."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from app.adapters.agents.process import Completed
from app.adapters.github.pull_requests import GhPullRequests
from app.domain.errors import UpstreamUnavailable
from app.domain.ports.devboard import PullRequest, PullRequestState

REPO = "acme/radar"
BRANCH = "agent/task-00000000-0000-0000-0000-000000000007"
URL = "https://github.com/acme/radar/pull/12"
MERGED_AT = "2026-01-02T03:04:05Z"


class FakeGh:
    """Stands in for `capture`: returns a scripted result or raises, and records each call."""

    def __init__(
        self, *, code: int = 0, stdout: bytes = b"[]", error: BaseException | None = None
    ) -> None:
        self._result = Completed(code=code, stdout=stdout)
        self._error = error
        self.argv: list[str] = []
        self.env: dict[str, str] = {}
        self.timeout_s = 0.0
        self.cwd: Path | None = None

    async def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout_s: float
    ) -> Completed:
        self.argv = list(argv)
        self.env = dict(env)
        self.timeout_s = timeout_s
        self.cwd = cwd
        if self._error is not None:
            raise self._error
        return self._result


def listing(*items: dict[str, object]) -> bytes:
    return json.dumps(list(items)).encode()


def item(
    state: str = "OPEN", *, url: object = URL, number: object = 12, merged_at: object = None
) -> dict[str, object]:
    return {"url": url, "number": number, "state": state, "mergedAt": merged_at}


async def find_with(gh: FakeGh, repo: str = REPO) -> PullRequest | None:
    return await GhPullRequests(repo, gh="gh-test", run=gh).find(BRANCH)


async def test_runs_one_read_only_gh_pr_list_for_the_branch() -> None:
    gh = FakeGh(stdout=listing(item()))

    await find_with(gh)

    assert gh.argv == [
        "gh-test",
        "pr",
        "list",
        "--repo",
        REPO,
        "--head",
        BRANCH,
        "--state",
        "all",
        "--json",
        "url,number,state,mergedAt",
        "--limit",
        "1",
    ]
    assert gh.timeout_s > 0


async def test_the_environment_is_minimal_and_never_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example.test/x")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_fake_token_value")
    monkeypatch.setenv("PATH", "/usr/bin")
    gh = FakeGh()

    await find_with(gh)

    assert "DATABASE_URL" not in gh.env
    assert "GITHUB_TOKEN" not in gh.env
    assert gh.env["PATH"] == "/usr/bin"
    assert gh.env["GH_PROMPT_DISABLED"] == "1"


@pytest.mark.parametrize(
    ("state", "merged_at", "expected"),
    [
        ("OPEN", None, PullRequestState.OPEN),
        ("CLOSED", None, PullRequestState.CLOSED),
        ("MERGED", MERGED_AT, PullRequestState.MERGED),
    ],
)
async def test_parses_each_state(state: str, merged_at: object, expected: PullRequestState) -> None:
    gh = FakeGh(stdout=listing(item(state, merged_at=merged_at)))

    found = await find_with(gh)

    assert found == PullRequest(url=URL, number=12, state=expected)


async def test_no_pr_for_the_branch_is_none() -> None:
    assert await find_with(FakeGh(stdout=b"[]\n")) is None


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"not json",
        b"{}",
        b'"text"',
        listing(item(), item()),
        json.dumps([[]]).encode(),
        listing({"url": URL, "number": 12, "state": "OPEN"}),
        listing({**item(), "extra": 1}),
        listing(item("DRAFT")),
        listing(item("open")),
        listing(item(number="12")),
        listing(item(number=True)),
        listing(item(number=0)),
        listing(item(number=13)),
        listing(item(merged_at=5)),
        listing(item(url=None)),
        b"\xff\xfe",
    ],
)
async def test_unexpected_output_is_upstream_unavailable(stdout: bytes) -> None:
    with pytest.raises(UpstreamUnavailable, match="unexpected output"):
        await find_with(FakeGh(stdout=stdout))


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/other/pull/12",
        "https://github.com/acme/radar/issues/12",
        "http://github.com/acme/radar/pull/12",
        "https://github.com.example.test/acme/radar/pull/12",
        "https://github.com/acme/radar/pull/12/files",
        "https://github.com/acme/radar/pull/12\n",
        "https://github.com/acme/radarX/pull/12",
        "https://github.com/acme/radar/pull/",
        "javascript:alert(1)",
    ],
)
async def test_a_url_outside_the_repo_is_refused(url: str) -> None:
    with pytest.raises(UpstreamUnavailable, match="unexpected output"):
        await find_with(FakeGh(stdout=listing(item(url=url))))


async def test_signed_out_gh_is_upstream_unavailable() -> None:
    with pytest.raises(UpstreamUnavailable, match="gh is not signed in"):
        await find_with(FakeGh(code=4, stdout=b"token=secret-value"))


async def test_a_failing_gh_names_only_the_exit_code() -> None:
    with pytest.raises(UpstreamUnavailable) as raised:
        await find_with(FakeGh(code=1, stdout=b"token=secret-value"))

    assert raised.value.detail == "gh pr list failed (exit 1)"


async def test_a_missing_gh_is_upstream_unavailable() -> None:
    with pytest.raises(UpstreamUnavailable, match="gh is not installed"):
        await find_with(FakeGh(error=FileNotFoundError("gh-test")))


async def test_a_hung_gh_is_upstream_unavailable() -> None:
    with pytest.raises(UpstreamUnavailable, match="timed out"):
        await find_with(FakeGh(error=TimeoutError()))


@pytest.mark.parametrize("repo", ["", "acme", "acme/radar/x", "-acme/radar", "acme/ra dar"])
def test_a_malformed_repo_is_refused(repo: str) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        GhPullRequests(repo)


@pytest.mark.parametrize("branch", ["", "--web", "agent/task 1", "agent/../main"])
async def test_a_malformed_branch_is_refused(branch: str) -> None:
    gh = FakeGh()

    with pytest.raises(ValueError, match="branch"):
        await GhPullRequests(REPO, run=gh).find(branch)
    assert gh.argv == []


async def test_the_real_capture_reports_a_missing_binary() -> None:
    missing = GhPullRequests(REPO, gh="jobradar-no-such-gh-binary")

    with pytest.raises(UpstreamUnavailable, match="gh is not installed"):
        await missing.find(BRANCH)
