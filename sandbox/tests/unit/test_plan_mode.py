"""Plan runs (AGENT_MODE=plan) and the dev-mode test-edit rule, without the SDK or network."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import agent_policy as ap
import agent_runner as ar
import coordinator_client as cc
import handoff
import llm_usage
import plan_contract
import pytest
import run_log

ENV = {
    "TASK_ID": "T-1",
    "RUN_ID": "R-1",
    "COORDINATOR_URL": "http://coordinator:8100",
    "COORDINATOR_TOKEN": "jrc1.fake.fake",
    "ANTHROPIC_BASE_URL": "http://llm-proxy:8080",
    "ANTHROPIC_AUTH_TOKEN": "jrt2.fake.fake",
}
TWO_USD = 2_000_000
TASK = {
    "task": {"id": "T-1", "labels": ["ui", "modify-tests:backend/tests/unit/devboard/"]},
    "brief": "Add a badge.",
    "next_cursor": None,
}
PLAN: dict[str, object] = {
    "task_id": "T-1",
    "mode": "multi",
    "subtasks": [
        {
            "id": "tests",
            "title": "Failing tests",
            "agent": "qa-expert",
            "lease_paths": ["web/src/features/board/"],
            "depends_on": [],
            "test_only": True,
        },
        {
            "id": "ui",
            "title": "Badge",
            "agent": "frontend-engineer",
            "lease_paths": ["web/src/features/board/"],
            "depends_on": ["tests"],
            "test_only": False,
        },
    ],
    "acceptance_criteria": [
        {
            "test_id": "web/src/features/board/Card.test.tsx::shows the badge",
            "expectation": "The card shows a priority badge.",
        }
    ],
    "tests_to_change": [],
    "budget_usd": 2.5,
    "budget_minutes": 30,
    "risk": "low",
}


@dataclass
class FakeTransport:
    responses: dict[str, tuple[int, object]] = field(default_factory=dict[str, tuple[int, object]])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        path = url.split("8100", 1)[1].split("?", 1)[0]
        self.calls.append((method, path, json.loads(body) if body else None))
        status, payload = self.responses.get(path, (200, {"ok": True}))
        return status, {"content-type": "application/json"}, json.dumps(payload).encode()


def _client(transport: FakeTransport) -> cc.CoordinatorClient:
    return cc.CoordinatorClient("http://coordinator:8100", "jrc1.fake.fake", transport=transport)


def _argv(tmp_path: Path) -> list[str]:
    out = str(tmp_path / "agent" / "result.json")
    return ["--base-dir", str(tmp_path / "base"), "--out", out, "--log-dir", str(tmp_path / "out")]


def _plan_settings(tmp_path: Path) -> ar.Settings:
    plan_out = str(tmp_path / "out" / "plan.json")
    return ar.load_settings(ENV, [*_argv(tmp_path), "--mode", "plan", "--plan-out", plan_out])


_NO_PROXY = llm_usage.UsageClient("", "")


# ------------------------------------------------------------------ settings and options


def test_plan_settings_need_a_plan_out(tmp_path: Path) -> None:
    s = _plan_settings(tmp_path)
    assert s.mode == "plan" and s.plan_out == tmp_path / "out" / "plan.json"
    assert ar.load_settings(ENV, _argv(tmp_path)).mode == "dev"
    with pytest.raises(ar.SettingsError):
        ar.load_settings(ENV, [*_argv(tmp_path), "--mode", "plan"])


def test_plan_options_are_read_only_tools(tmp_path: Path) -> None:
    opts = ar.option_kwargs(_plan_settings(tmp_path), "SYSTEM", ENV)
    assert opts["tools"] == list(ap.PLAN_TOOLS)
    assert "Edit" not in opts["tools"] and "Write" not in opts["tools"]
    assert opts["allowed_tools"] == [] and opts["setting_sources"] == []
    assert {"WebFetch", "Task", "Edit", "Write"} <= set(opts["disallowed_tools"])


def test_check_tool_follows_the_plan_mode_policy() -> None:
    tracker = ap.RunTracker(token_budget=10, plan_mode=True)
    tracker.add_leases(["web/"])  # never granted in plan mode; still no edits
    assert not ar.check_tool(tracker, "Edit", {"file_path": "web/src/a.ts"}).allowed
    assert not ar.check_tool(tracker, "mcp__coordinator__claim_paths", {}).allowed
    assert not ar.check_tool(tracker, "Bash", {"command": "uv run pytest"}).allowed
    assert ar.check_tool(tracker, "Bash", {"command": "git grep -n badge"}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__submit_plan", {}).allowed
    assert tracker.denials == 3


def test_check_tool_dev_refuses_existing_tests_without_a_label() -> None:
    tracker = ap.RunTracker(token_budget=10)
    tracker.add_leases(["backend/"])
    tracker.existing_tests = frozenset({"backend/tests/unit/devboard/test_board.py"})
    edit = {"file_path": "backend/tests/unit/devboard/test_board.py"}
    assert not ar.check_tool(tracker, "Edit", edit).allowed
    tracker.modify_tests = ("backend/tests/unit/devboard",)
    assert ar.check_tool(tracker, "Edit", edit).allowed


def test_existing_test_files_come_from_the_base_file_list(tmp_path: Path) -> None:
    names = [
        "backend/tests/unit/test_a.py",
        "backend/src/app/a.py",
        "web/src/a.test.tsx",
        "web/src/a.tsx",
        "web/tests/e2e/a.spec.ts",
        "",
    ]
    listing = tmp_path / "base-files"  # `git ls-files -z` at the base commit (entrypoint)
    listing.write_bytes("\0".join(names).encode())
    assert ar.existing_test_files(listing) == frozenset(
        {"backend/tests/unit/test_a.py", "web/src/a.test.tsx", "web/tests/e2e/a.spec.ts"}
    )
    with pytest.raises(OSError):
        ar.existing_test_files(tmp_path / "missing")
    s = ar.load_settings(ENV, [*_argv(tmp_path), "--base-files", str(listing)])
    assert s.base_files == listing
    assert ar.load_settings(ENV, _argv(tmp_path)).base_files is None


# ------------------------------------------------------------------ start


def test_begin_reads_labels_and_asks_for_a_plan(tmp_path: Path) -> None:
    transport = FakeTransport({"/v1/task": (200, TASK)})
    tracker = ap.RunTracker(token_budget=1000, plan_mode=True)
    log = run_log.RunLog(tmp_path / "out")
    turn = ar.begin(_plan_settings(tmp_path), _client(transport), tracker, log, _NO_PROXY)
    assert "submit_plan" in turn.rsplit(">", 1)[1]
    assert tracker.modify_tests == ("backend/tests/unit/devboard",)
    assert [p for _, p, _ in transport.calls] == ["/v1/task", "/v1/steps"]


# ------------------------------------------------------------------ finish


def _finish(
    tmp_path: Path, outcome: ar.LoopOutcome, submitted: bool
) -> tuple[dict[str, object], FakeTransport]:
    transport = FakeTransport()
    tracker = ap.RunTracker(token_budget=3_000_000, plan_mode=True)
    tracker.plan_submitted = submitted
    log = run_log.RunLog(tmp_path / "out")
    spend = llm_usage.Spend(300_000, TWO_USD, 6, 0)
    settings = _plan_settings(tmp_path)
    return ar.finish(settings, tracker, outcome, spend, log, _client(transport)), transport


def test_a_plan_run_never_posts_a_handoff(tmp_path: Path) -> None:
    result, transport = _finish(tmp_path, ar.LoopOutcome("end_turn", 6), True)
    assert transport.calls == []
    assert result["plan_submitted"] is True and result["runner_posted_handoff"] is False
    assert result["detail"] == "Plan submitted after 6 turns ($0.30 of $2.00)."


def test_a_plan_run_that_ends_without_a_plan_says_so(tmp_path: Path) -> None:
    result, transport = _finish(tmp_path, ar.LoopOutcome("end_turn", 6), False)
    assert transport.calls == []
    assert str(result["detail"]).startswith("Stopped: the agent ended without submit_plan")


def test_main_in_plan_mode_stops_before_the_model_without_a_coordinator(
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
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setitem(sys.modules, "agent_sdk", None)
    plan_out = tmp_path / "out" / "plan.json"
    argv = [*_argv(tmp_path), "--mode", "plan", "--plan-out", str(plan_out)]
    assert ar.main(argv) == ar.EXIT_COORDINATOR
    assert not plan_out.exists()


# ------------------------------------------------------------------ handoff.json


def _result(tmp_path: Path, stop: str = "end_turn", submitted: bool = True) -> Path:
    path = tmp_path / "result.json"
    detail = "Plan submitted after 6 turns ($0.30 of $2.00)." if submitted else "Stopped: x."
    path.write_text(json.dumps({"stop": stop, "plan_submitted": submitted, "detail": detail}))
    return path


def test_plan_handoff_is_done_with_a_valid_plan(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_contract.write(PLAN, plan_path)
    agent = handoff.read_agent_result(_result(tmp_path))
    h = handoff.build_plan("T-1", plan_path, agent, [])
    assert h["status"] == "done"
    assert set(h) >= {"status", "summary", "changed_paths", "tests", "skipped_checks"}
    summary = str(h["summary"])
    assert "2 subtasks" in summary and "1 acceptance test" in summary
    assert h["changed_paths"] == []
    assert "plan run: no tests run" in cc.as_list(h["skipped_checks"])


@pytest.mark.parametrize("content", [None, "not json", json.dumps({**PLAN, "risk": "huge"})])
def test_plan_handoff_fails_without_a_valid_plan(tmp_path: Path, content: str | None) -> None:
    plan_path = tmp_path / "plan.json"
    if content is not None:
        plan_path.write_text(content)
    agent = handoff.read_agent_result(_result(tmp_path, stop="budget", submitted=False))
    h = handoff.build_plan("T-1", plan_path, agent, [])
    assert h["status"] == "failed"
    assert str(h["summary"]).startswith("Stopped: x.")


def test_plan_handoff_cli(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_contract.write(PLAN, plan_path)
    out = tmp_path / "handoff.json"
    args = ["--task-id", "T-1", "--out", str(out), "--plan-mode", "--plan-json", str(plan_path)]
    assert handoff.main([*args, "--agent-result", str(_result(tmp_path))]) == 0
    assert json.loads(out.read_text())["status"] == "done"
