"""Review runs (AGENT_MODE=review): settings, model, read-only policy, prompt, submit_review,
finish and handoff.json. Also the writer's rework turn (``review_blockers`` in get_task).

No SDK and no network: the coordinator is a fake transport.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import agent_policy as ap
import agent_prompt
import agent_runner as ar
import coordinator_client as cc
import coordinator_tools as ct
import handoff
import llm_usage
import pytest
import review_contract
import run_log
from test_triage_mode import ENV, FakeTransport

BASE = "d" * 40
CHANGED = frozenset({"web/src/features/board/Card.tsx"})
TASK: dict[str, object] = {
    "task": {"id": "T-1", "labels": list[str]()},
    "brief": "The card badge is missing.",
    "acceptance_criteria": [{"test_id": "web/src/a.test.tsx::shows", "expectation": "Shows."}],
    "review": {"base_sha": BASE, "branch": "agent/t1-r1", "round": 1},
    "previous_blockers": list[object](),
}
FINDING: dict[str, object] = {
    "severity": "BLOCKER",
    "rubric_id": "TEST-01",
    "path": "web/src/features/board/Card.tsx",
    "evidence": "No test renders the badge.",
    "fix": "Add one.",
}
REVIEW: dict[str, object] = {"summary": "The badge has no test.", "findings": [FINDING]}


def _client(transport: FakeTransport) -> cc.CoordinatorClient:
    return cc.CoordinatorClient("http://coordinator:8100", "jrc1.fake.fake", transport=transport)


def _argv(tmp_path: Path) -> list[str]:
    out = str(tmp_path / "agent" / "result.json")
    return ["--base-dir", str(tmp_path / "base"), "--out", out, "--log-dir", str(tmp_path / "out")]


def _listing(tmp_path: Path) -> Path:
    path = tmp_path / "changed-files"
    path.write_bytes(b"\0".join(name.encode() for name in sorted(CHANGED)) + b"\0")
    return path


def _review_argv(tmp_path: Path) -> list[str]:
    out = str(tmp_path / "out" / "review.json")
    listing = str(_listing(tmp_path))
    return [*_argv(tmp_path), "--mode", "review", "--review-out", out, "--changed-files", listing]


def _settings(tmp_path: Path, env: dict[str, str] | None = None) -> ar.Settings:
    return ar.load_settings(env or ENV, _review_argv(tmp_path))


def _tracker() -> ap.RunTracker:
    return ap.RunTracker(token_budget=1000, review_mode=True)


# ------------------------------------------------------------------ settings and model


def test_review_settings_need_a_review_out_and_the_changed_files(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    assert s.mode == "review"
    assert s.is_review
    assert s.is_read_only
    assert s.review_out == tmp_path / "out" / "review.json"
    assert s.changed_files == tmp_path / "changed-files"
    with pytest.raises(ar.SettingsError):
        ar.load_settings(ENV, [*_argv(tmp_path), "--mode", "review"])
    out = str(tmp_path / "review.json")
    with pytest.raises(ar.SettingsError):
        ar.load_settings(ENV, [*_argv(tmp_path), "--mode", "review", "--review-out", out])


def test_review_runs_on_sonnet_unless_the_env_picks_a_model(tmp_path: Path) -> None:
    assert _settings(tmp_path).model == "claude-sonnet-5-5"
    picked = {**ENV, "AGENT_MODEL": "claude-opus-5-5"}
    assert _settings(tmp_path, picked).model == "claude-opus-5-5"


def test_review_options_are_read_only_tools(tmp_path: Path) -> None:
    opts = ar.option_kwargs(_settings(tmp_path), "SYSTEM", ENV)
    assert opts["tools"] == list(ap.PLAN_TOOLS)
    assert {"Edit", "Write", "WebFetch", "Task"} <= set(opts["disallowed_tools"])


# ------------------------------------------------------------------ policy


@pytest.mark.parametrize(
    "command",
    [
        f"git diff {BASE}..HEAD",
        f"git diff --stat {BASE}..HEAD",
        "git diff --stat",
        f"git diff {BASE}..HEAD -- web/src/features/board/Card.tsx",
        "git log --oneline -5",
        "git show HEAD:web/src/features/board/Card.tsx",
        "git grep -n badge",
    ],
)
def test_the_reviewer_may_run_read_only_git(command: str) -> None:
    decision = ar.check_tool(_tracker(), "Bash", {"command": command})
    assert decision.allowed, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "git commit -am x",
        "git checkout main",
        "git diff --output=/tmp/x",
        "git diff --ext-diff",
        "cd backend && uv run pytest -q tests/unit/test_x.py",
        "scripts/check.sh --only pytest",
        f"git diff {BASE}..HEAD | head",
    ],
)
def test_the_reviewer_refuses_everything_else(command: str) -> None:
    assert not ar.check_tool(_tracker(), "Bash", {"command": command}).allowed


def test_check_tool_follows_the_review_policy() -> None:
    tracker = _tracker()
    assert ar.check_tool(tracker, "Read", {"file_path": "web/src/a.ts"}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__submit_review", {}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__get_task", {}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__post_step", {}).allowed
    for name in ("Edit", "Write"):
        decision = ar.check_tool(tracker, name, {"file_path": "web/src/a.ts"})
        assert not decision.allowed
        assert "read-only" in decision.reason
    for tool in ("claim_paths", "post_handoff", "submit_plan", "submit_triage"):
        assert not ar.check_tool(tracker, f"mcp__coordinator__{tool}", {}).allowed
    assert tracker.denials == 6


def test_other_modes_can_not_submit_a_review() -> None:
    for tracker in (
        ap.RunTracker(token_budget=10),
        ap.RunTracker(token_budget=10, plan_mode=True),
        ap.RunTracker(token_budget=10, triage_mode=True),
    ):
        assert not ar.check_tool(tracker, "mcp__coordinator__submit_review", {}).allowed


# ------------------------------------------------------------------ prompt


def test_the_review_prompt_is_isolated_with_blocker_rules() -> None:
    system = agent_prompt.build_system_prompt({"AGENTS.md": "# A"}, mode="reviewer")
    assert system.startswith(agent_prompt.REVIEW_INSTRUCTIONS)
    assert '<repo-file path="AGENTS.md">' in system  # the writer's standards pack
    text = agent_prompt.REVIEW_INSTRUCTIONS
    for needle in (
        "submit_review",
        "BLOCKER",
        "NIT",
        "rubric_id",
        "test_id",
        "repro",
        "review-rubric.yaml",
        "git diff <base_sha>..HEAD",
        "previous_blockers",
        "never see the writer",
        "read-only",
    ):
        assert needle in text
    assert "claim_paths" not in text


def test_the_review_user_turn_asks_for_submit_review() -> None:
    turn = agent_prompt.build_user_turn(TASK, "T-1", "R-1", lambda: "n0nce", mode="reviewer")
    data = turn.split('id="n0nce">', 1)[1].rsplit('</untrusted-task-data id="n0nce">', 1)[0]
    assert BASE in data
    closing = turn.rsplit(">", 1)[1]
    assert "submit_review" in closing
    assert "git diff" in closing


def test_a_rework_turn_says_to_fix_exactly_the_blockers() -> None:
    blockers = [{"path": "web/src/a.tsx", "line": 3, "citation": "TEST-01", "evidence": "x"}]
    task: dict[str, object] = {**TASK, "review_blockers": blockers}
    turn = agent_prompt.build_user_turn(task, "T-1", "R-1", lambda: "n0nce", mode="dev")
    closing = turn.rsplit(">", 1)[1]
    assert "review_blockers" in closing
    assert "nothing else" in closing
    plain = agent_prompt.build_user_turn(TASK, "T-1", "R-1", lambda: "n0nce", mode="dev")
    assert "review_blockers" not in plain.rsplit(">", 1)[1]
    assert "review_blockers" in agent_prompt.WRITER_INSTRUCTIONS


# ------------------------------------------------------------------ submit_review


def _review_tools(
    tmp_path: Path,
) -> tuple[ct.CoordinatorTools, ap.RunTracker, FakeTransport, Path]:
    out = tmp_path / "out" / "review.json"
    transport = FakeTransport()
    tracker = _tracker()
    sink = ct.ReviewSink(task_id="T-1", out=out, changed_files=CHANGED)
    tools = ct.CoordinatorTools(_client(transport), tracker, review_sink=sink)
    return tools, tracker, transport, out


def _call(tools: ct.CoordinatorTools, name: str, args: dict[str, object]) -> dict[str, object]:
    return asyncio.run(tools.call(name, args))


def _text(result: dict[str, object]) -> str:
    content = cc.as_list(result["content"])
    first = cc.as_object(content[0]) or {}
    return str(first["text"])


def test_review_specs_are_read_only_plus_submit_review(tmp_path: Path) -> None:
    tools, _, transport, _ = _review_tools(tmp_path)
    assert tuple(s.name for s in tools.specs) == ap.REVIEW_COORDINATOR_TOOLS
    for spec in tools.specs:
        assert spec.input_schema["additionalProperties"] is False
    for name in ("claim_paths", "post_handoff", "submit_plan", "submit_triage"):
        assert _call(tools, name, {"paths": ["web/"]})["is_error"] is True
    assert transport.calls == []


def test_submit_review_writes_the_contract_file(tmp_path: Path) -> None:
    tools, tracker, transport, out = _review_tools(tmp_path)
    result = _call(tools, "submit_review", {"review": REVIEW})
    assert result["is_error"] is False, _text(result)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert list(written) == list(review_contract.TOP_KEYS)
    assert written["task_id"] == "T-1"
    assert tracker.review_submitted is True
    assert "1 blocker" in _text(result)
    assert transport.calls == []  # the review goes to /out, not the coordinator


def test_a_finding_on_an_unchanged_file_is_a_fixable_tool_error(tmp_path: Path) -> None:
    tools, tracker, _, out = _review_tools(tmp_path)
    finding = {**FINDING, "path": "web/src/other.tsx"}
    result = _call(tools, "submit_review", {"review": {**REVIEW, "findings": [finding]}})
    assert result["is_error"] is True
    text = _text(result)
    assert "not a file this branch changed" in text
    assert "submit_review again" in text
    assert not out.exists()
    assert tracker.review_submitted is False
    assert _call(tools, "submit_review", {})["is_error"] is True


# ------------------------------------------------------------------ start and finish


def test_begin_builds_the_review_turn(tmp_path: Path) -> None:
    transport = FakeTransport({"/v1/task": (200, TASK)})
    log = run_log.RunLog(tmp_path / "out")
    no_proxy = llm_usage.UsageClient("", "")
    turn = ar.begin(_settings(tmp_path), _client(transport), _tracker(), log, no_proxy)
    assert "submit_review" in turn.rsplit(">", 1)[1]


def _finish(tmp_path: Path, submitted: bool) -> tuple[dict[str, object], FakeTransport]:
    transport = FakeTransport()
    tracker = _tracker()
    tracker.review_submitted = submitted
    spend = llm_usage.Spend(300_000, 1_000_000, 4, 0)
    log = run_log.RunLog(tmp_path / "out")
    outcome = ar.LoopOutcome("end_turn", 5)
    result = ar.finish(_settings(tmp_path), tracker, outcome, spend, log, _client(transport))
    return result, transport


def test_a_review_run_never_posts_a_handoff(tmp_path: Path) -> None:
    result, transport = _finish(tmp_path, True)
    assert transport.calls == []
    assert result["review_submitted"] is True
    assert result["detail"] == "Review submitted after 5 turns ($0.30 of $1.00)."


def test_a_review_run_that_ends_without_submitting_says_so(tmp_path: Path) -> None:
    result, _ = _finish(tmp_path, False)
    assert str(result["detail"]).startswith("Stopped: the agent ended without submit_review")


def test_main_in_review_mode_stops_before_the_model_without_a_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "base" / "docs" / "standards").mkdir(parents=True)
    (tmp_path / "base" / "AGENTS.md").write_text("# AGENTS\n")
    (tmp_path / "base" / "docs" / "standards" / "testing.md").write_text("# testing\n")
    env = {
        **ENV,
        "COORDINATOR_URL": "http://127.0.0.1:9",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "agent_sdk", None)
    assert ar.main(_review_argv(tmp_path)) == ar.EXIT_COORDINATOR
    assert not (tmp_path / "out" / "review.json").exists()


# ------------------------------------------------------------------ handoff.json


def _result(tmp_path: Path, submitted: bool) -> Path:
    path = tmp_path / "result.json"
    detail = "Review submitted after 5 turns ($0.30 of $1.00)." if submitted else "Stopped: x."
    path.write_text(
        json.dumps({"stop": "end_turn", "review_submitted": submitted, "detail": detail})
    )
    return path


def test_review_handoff_is_done_with_a_valid_review(tmp_path: Path) -> None:
    path = tmp_path / "review.json"
    review_contract.write(review_contract.validate(REVIEW, "T-1", CHANGED), path)
    agent = handoff.read_agent_result(_result(tmp_path, True))
    h = handoff.build_review("T-1", path, CHANGED, agent, [])
    assert h["status"] == "done"
    summary = str(h["summary"])
    assert "1 finding (1 blocker, 0 nits)" in summary
    assert "badge" not in summary  # free text stays out of the summary


@pytest.mark.parametrize("content", [None, "not json", json.dumps({**REVIEW, "extra": 1})])
def test_review_handoff_fails_without_a_valid_review(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "review.json"
    if content is not None:
        path.write_text(content)
    agent = handoff.read_agent_result(_result(tmp_path, False))
    h = handoff.build_review("T-1", path, CHANGED, agent, [])
    assert h["status"] == "failed"
    assert str(h["summary"]).startswith("Stopped: x. No valid review was written.")


def test_review_handoff_cli(tmp_path: Path) -> None:
    path = tmp_path / "review.json"
    review_contract.write(review_contract.validate(REVIEW, "T-1", CHANGED), path)
    out = tmp_path / "handoff.json"
    listing = str(_listing(tmp_path))
    args = ["--task-id", "T-1", "--out", str(out), "--review-mode", "--review-json", str(path)]
    agent = ["--agent-result", str(_result(tmp_path, True))]
    assert handoff.main([*args, "--changed-files", listing, *agent]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "done"
    with pytest.raises(SystemExit):
        handoff.main(["--task-id", "T-1", "--out", str(out), "--review-mode"])
