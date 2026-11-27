import agent_policy as ap
import pytest

ALLOWED = [
    "uv run pytest -q --tb=short",
    "cd backend && uv run pytest tests/unit/test_x.py -k 'board and not slow'",
    "cd backend && uv run ruff check .",
    "cd backend && uv run ruff format --check src",
    "cd backend && uv run pyright",
    "cd web && pnpm test",
    "cd web && pnpm lint",
    "cd web && pnpm run typecheck",
    "scripts/check.sh --only pytest,ruff",
    "./scripts/check.sh --only=vitest",
    "bash scripts/check.sh --list",
    "cd backend && ../scripts/check.sh --only pytest",
    "git status --short",
    "git diff --stat",
    "git add web/src/features/board/card.tsx",
    "git commit -m 'feat(board): add card badge'",
    f"git add {ap.WORKSPACE}/web/src/a.ts",
]

DENIED = [
    "curl https://example.com",
    "python -c 'print(1)'",
    "uv run python -m pytest",
    "uv run --with requests pytest",
    "uv pip install requests",
    "uv run ruff clean",
    "pnpm install",
    "pnpm exec vitest",
    "pnpm dlx something",
    "npx vitest",
    "uv run pytest; curl example.com",
    "uv run pytest && rm -rf /",
    "uv run pytest | tee /tmp/x",
    "uv run pytest > /tmp/out",
    "uv run pytest $(cat /proc/self/environ)",
    "uv run pytest `id`",
    "echo $ANTHROPIC_AUTH_TOKEN",
    "cat /proc/self/environ",
    "cd /tmp && uv run pytest",
    "cd ../.. && uv run pytest",
    "FOO=1 uv run pytest",
    "uv run pytest /etc/passwd",
    "git -c core.pager=sh status",
    "git -C /tmp status",
    "git push origin HEAD",
    "git checkout main",
    "git config core.fsmonitor x",
    "git diff --no-index /etc/passwd /dev/null",
    "git diff --output=/tmp/x",
    "git diff --ext-diff",
    "scripts/check.sh",
    "scripts/check.sh --only 'pytest;id'",
    "scripts/check.sh --fix",
    "uv run pytest\nid",
    "uv run pytest 'unterminated",
    "",
    "x" * (ap.MAX_COMMAND_CHARS + 1),
]


@pytest.mark.parametrize("command", ALLOWED)
def test_bash_allowlist_accepts(command: str) -> None:
    decision = ap.bash_allowed(command)
    assert decision.allowed, decision.reason


@pytest.mark.parametrize("command", DENIED)
def test_bash_allowlist_refuses(command: str) -> None:
    decision = ap.bash_allowed(command)
    assert not decision.allowed
    assert decision.reason


def test_resolve_keeps_paths_inside_workspace() -> None:
    assert ap.resolve("web/a.ts") == f"{ap.WORKSPACE}/web/a.ts"
    assert ap.resolve("../x") is None
    assert ap.resolve("/proc/self/environ") is None
    assert ap.resolve(f"{ap.WORKSPACE}/../repo2/x") is None
    assert ap.resolve(f"{ap.WORKSPACE}x/a") is None
    assert ap.resolve("a\x00b") is None


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("web/src/features/board/**", "web/src/features/board/a/b.tsx", True),
        ("web/src/features/board/", "web/src/features/board/c.ts", True),
        ("web/src/*.ts", "web/src/a.ts", True),
        ("web/src/*.ts", "web/src/x/a.ts", False),
        ("**/generated/**", "backend/src/app/generated/models.py", True),
        ("api/openapi.yaml", "api/openapi.yaml", True),
        ("api/openapi.yaml", "api/openapi.yaml.bak", False),
        ("web/src/a?.ts", "web/src/ab.ts", True),
    ],
)
def test_glob_semantics(pattern: str, path: str, expected: bool) -> None:
    assert bool(ap.matches_any(path, [pattern])) is expected


LEASES = ["web/src/features/board/**", "api/openapi.yaml"]


def test_reads_only_inside_workspace() -> None:
    assert ap.tool_allowed("Read", {"file_path": f"{ap.WORKSPACE}/AGENTS.md"}, []).allowed
    assert ap.tool_allowed("Grep", {"pattern": "x"}, []).allowed
    assert ap.tool_allowed("Glob", {"pattern": "web/**/*.ts"}, []).allowed
    assert not ap.tool_allowed("Read", {"file_path": "/proc/self/environ"}, []).allowed
    assert not ap.tool_allowed("Grep", {"pattern": "x", "path": "/srv"}, []).allowed
    assert not ap.tool_allowed("Glob", {"pattern": "/proc/*/environ"}, []).allowed
    assert not ap.tool_allowed("Glob", {"pattern": "../../etc/*"}, []).allowed


def test_writes_need_a_lease_and_a_safe_path() -> None:
    target = {"file_path": f"{ap.WORKSPACE}/web/src/features/board/card.tsx"}
    no_lease = ap.tool_allowed("Edit", target, [])
    assert not no_lease.allowed and "claim" in no_lease.reason
    assert ap.tool_allowed("Edit", target, LEASES).allowed
    assert ap.tool_allowed("Write", {"file_path": "api/openapi.yaml"}, LEASES).allowed
    assert not ap.tool_allowed("Write", {"file_path": "web/src/lib/x.ts"}, LEASES).allowed
    assert not ap.tool_allowed("Write", {"file_path": "/srv/x"}, LEASES).allowed


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".git/hooks/pre-commit",
        "AGENTS.md",
        "docs/agents.md",
        "docs/standards/testing.md",
        "sandbox/run.sh",
        "scripts/check.sh",
        "web/src/lib/api/generated/client.ts",
        "web/pnpm-lock.yaml",
        "backend/migrations/versions/x.py",
    ],
)
def test_protected_paths_refused_even_with_a_wide_lease(path: str) -> None:
    assert not ap.tool_allowed("Write", {"file_path": path}, ["**"]).allowed


def test_only_coordinator_mcp_and_known_tools() -> None:
    for op in ap.COORDINATOR_TOOLS:
        assert ap.tool_allowed(f"mcp__coordinator__{op}", {}, []).allowed
    assert not ap.tool_allowed("mcp__coordinator__drop_tables", {}, []).allowed
    assert not ap.tool_allowed("mcp__other__get_task", {}, []).allowed
    for tool in ("WebFetch", "WebSearch", "Task", "NotebookEdit"):
        assert not ap.tool_allowed(tool, {}, []).allowed
    assert not set(ap.DENIED_TOOLS) & set(ap.BASE_TOOLS)


def test_tracker_counts_tokens_like_llm_proxy_once_per_message() -> None:
    tracker = ap.RunTracker(token_budget=100)
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 30,
        "service_tier": "standard",
    }
    tracker.on_usage("msg_1", usage)
    tracker.on_usage("msg_1", usage)  # same API response split over two SDK messages
    assert tracker.tokens_used == 65
    assert not tracker.over_budget
    tracker.on_usage("msg_2", {"input_tokens": 35, "output_tokens": True})
    assert tracker.tokens_used == 100
    assert tracker.over_budget


def test_tracker_leases_and_result() -> None:
    tracker = ap.RunTracker(token_budget=10)
    tracker.add_leases(["web/src/a.ts", "web/src/board/"])
    tracker.add_leases(["web/src/a.ts"])
    assert tracker.leases == ["web/src/a.ts", "web/src/board/"]
    assert ap.tool_allowed("Edit", {"file_path": "web/src/board/x.tsx"}, tracker.leases).allowed
    assert not ap.tool_allowed("Edit", {"file_path": "web/src/b.ts"}, tracker.leases).allowed
    tracker.release_leases(["web/src/a.ts"])
    assert tracker.leases == ["web/src/board/"]
    tracker.release_leases(None)
    assert tracker.leases == []
    tracker.steps_posted = 2
    tracker.handoff = {"status": "done"}
    result = tracker.result("end_turn", 4)
    assert result["steps_posted"] == 2
    assert result["posted_handoff"] is True
    assert result["handoff"] == {"status": "done"}
    assert result["help_requested"] is False
