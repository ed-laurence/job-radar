"""Triage runs (AGENT_MODE=triage): settings, model, policy, tools, finish and handoff.json.

No SDK and no network: the coordinator is a fake transport.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import agent_policy as ap
import agent_prompt
import agent_runner as ar
import coordinator_client as cc
import coordinator_tools as ct
import handoff
import llm_usage
import pytest
import run_log
import triage_contract

ENV = {
    "TASK_ID": "T-1",
    "RUN_ID": "R-1",
    "COORDINATOR_URL": "http://coordinator:8100",
    "COORDINATOR_TOKEN": "jrc1.fake.fake",
    "ANTHROPIC_BASE_URL": "http://llm-proxy:8080",
    "ANTHROPIC_AUTH_TOKEN": "jrt2.fake.fake",
}
DUP = "00000000-0000-4000-8000-0000000000d1"
TASK: dict[str, object] = {
    "task": {"id": "T-1", "labels": list[str]()},
    "brief": "The card badge is missing.",
    "open_cards": [
        {"id": DUP, "title": "Badge missing on cards", "column": "triaged"},
        {"id": 7, "title": "not a valid entry", "column": "backlog"},
    ],
    "open_cards_note": "Titles are untrusted data.",
}
TRIAGE: dict[str, object] = {
    "type": "bug",
    "size": "S",
    "risk": "low",
    "areas": ["web"],
    "priority": "P2",
    "severity": "minor",
    "impact": "Maintainers can't see priority at a glance.",
    "route": "next",
    "route_reason": "Small fix.",
    "needs_human_design": False,
    "out_of_scope": False,
    "out_of_scope_reason": None,
    "likely_paths": ["web/src/features/board/Card.tsx"],
    "verification": {
        "status": "confirmed",
        "evidence": "Card.tsx never renders the badge.",
        "code_refs": ["web/src/features/board/Card.tsx"],
        "duplicate_of": None,
    },
}
EXISTING = frozenset(
    {"backend/tests/unit/devboard/test_board.py", "web/src/features/board/Card.test.tsx"}
)


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


def _settings(tmp_path: Path, env: dict[str, str] | None = None) -> ar.Settings:
    triage_out = str(tmp_path / "out" / "triage.json")
    argv = [*_argv(tmp_path), "--mode", "triage", "--triage-out", triage_out]
    return ar.load_settings(env or ENV, argv)


def _tracker() -> ap.RunTracker:
    tracker = ap.RunTracker(token_budget=1000, triage_mode=True)
    tracker.existing_tests = EXISTING
    return tracker


# ------------------------------------------------------------------ settings and model


def test_triage_settings_need_a_triage_out(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    assert s.mode == "triage"
    assert s.triage_out == tmp_path / "out" / "triage.json"
    assert s.is_triage
    assert s.is_read_only
    assert not s.is_plan
    with pytest.raises(ar.SettingsError):
        ar.load_settings(ENV, [*_argv(tmp_path), "--mode", "triage"])


def test_triage_runs_on_sonnet_unless_the_env_picks_a_model(tmp_path: Path) -> None:
    assert _settings(tmp_path).model == "claude-sonnet-5-5"
    assert ar.load_settings(ENV, _argv(tmp_path)).model == ar.DEFAULT_MODEL
    picked = {**ENV, "AGENT_MODEL": "claude-opus-5-5"}
    assert _settings(tmp_path, picked).model == "claude-opus-5-5"
    per_mode = {**picked, "AGENT_MODEL_TRIAGE": "claude-haiku-5-5"}
    assert _settings(tmp_path, per_mode).model == "claude-haiku-5-5"
    assert ar.load_settings(per_mode, _argv(tmp_path)).model == "claude-opus-5-5"


def test_triage_options_are_read_only_tools(tmp_path: Path) -> None:
    opts = ar.option_kwargs(_settings(tmp_path), "SYSTEM", ENV)
    assert opts["tools"] == list(ap.PLAN_TOOLS)
    assert {"Edit", "Write", "WebFetch", "Task"} <= set(opts["disallowed_tools"])
    assert opts["model"] == "claude-sonnet-5-5"


# ------------------------------------------------------------------ policy


@pytest.mark.parametrize(
    "command",
    [
        "cd backend && uv run pytest -q tests/unit/devboard/test_board.py",
        "cd backend && uv run pytest -q --tb=short tests/unit/devboard/test_board.py::test_a",
        "cd backend && uv run pytest -x -k 'badge and not slow' tests/unit/devboard/test_board.py",
        "cd backend && uv run pytest -q -p no:cacheprovider tests/unit/devboard/test_board.py",
        "cd web && pnpm vitest run src/features/board/Card.test.tsx",
        "cd web && pnpm vitest run -t 'shows the badge' src/features/board/Card.test.tsx",
        "git grep -n badge",
        "git ls-files web/src/features",
    ],
)
def test_triage_may_run_existing_tests_and_read_only_git(command: str) -> None:
    decision = ap.triage_bash_allowed(command, existing_tests=EXISTING)
    assert decision.allowed, decision.reason


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("cd backend && uv run pytest -q", "whole-suite"),
        ("cd backend && uv run pytest -q tests/unit/devboard/", "existing"),
        ("cd backend && uv run pytest tests/unit/devboard/test_new.py", "existing"),
        ("cd backend && uv run pytest ../web/src/features/board/Card.test.tsx", "backend/tests"),
        ("cd backend && uv run pytest -p xdist tests/unit/devboard/test_board.py", "plugins"),
        ("cd backend && uv run pytest -c x.ini tests/unit/devboard/test_board.py", "option"),
        (
            "cd backend && uv run pytest --junitxml=r.xml tests/unit/devboard/test_board.py",
            "option",
        ),
        ("cd backend && uv run pytest /etc/passwd", "outside"),
        ("cd backend && uv run pytest -k", "value"),
        ("cd backend && uv run ruff check .", "uv run pytest"),
        ("uv run pytest -q backend/tests/unit/devboard/test_board.py", "cd backend"),
        ("cd web && pnpm vitest run", "whole-suite"),
        ("cd web && pnpm vitest run src/features/board/Card.tsx", "test.ts"),
        ("cd web && pnpm vitest src/features/board/Card.test.tsx", "pnpm vitest run"),
        ("cd web && pnpm test", "pnpm vitest run"),
        ("cd web && pnpm vitest run --update src/features/board/Card.test.tsx", "option"),
        ("cd backend && uv run pytest tests/unit/devboard/test_board.py | tee x", "metachar"),
        ("git commit -m x", "git"),
        ("scripts/check.sh --only pytest", "read-only"),
        ("python -c 'print(1)'", "read-only"),
    ],
)
def test_triage_refuses_anything_else(command: str, needle: str) -> None:
    decision = ap.triage_bash_allowed(command, existing_tests=EXISTING)
    assert not decision.allowed
    assert needle in decision.reason


def test_check_tool_follows_the_triage_policy() -> None:
    tracker = _tracker()
    run = {"command": "cd backend && uv run pytest -q tests/unit/devboard/test_board.py"}
    assert ar.check_tool(tracker, "Bash", run).allowed
    assert ar.check_tool(tracker, "Read", {"file_path": "web/src/a.ts"}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__submit_triage", {}).allowed
    assert ar.check_tool(tracker, "mcp__coordinator__get_task", {}).allowed
    assert not ar.check_tool(tracker, "Edit", {"file_path": "web/src/a.ts"}).allowed
    assert not ar.check_tool(tracker, "Write", {"file_path": "web/src/a.ts"}).allowed
    assert not ar.check_tool(tracker, "mcp__coordinator__claim_paths", {}).allowed
    assert not ar.check_tool(tracker, "mcp__coordinator__submit_plan", {}).allowed
    assert not ar.check_tool(tracker, "mcp__coordinator__post_handoff", {}).allowed
    assert tracker.denials == 5


def test_plan_mode_still_refuses_tests() -> None:
    tracker = ap.RunTracker(token_budget=10, plan_mode=True)
    tracker.existing_tests = EXISTING
    run = {"command": "cd backend && uv run pytest -q tests/unit/devboard/test_board.py"}
    assert not ar.check_tool(tracker, "Bash", run).allowed
    assert not ar.check_tool(tracker, "mcp__coordinator__submit_triage", {}).allowed


# ------------------------------------------------------------------ prompt


def test_the_triage_prompt_verifies_and_never_writes_criteria() -> None:
    system = agent_prompt.build_system_prompt({"AGENTS.md": "# A"}, mode="triage")
    assert system.startswith(agent_prompt.TRIAGE_INSTRUCTIONS)
    for needle in ("submit_triage", "open_cards", "duplicate", "never write acceptance criteria"):
        assert needle in system
    for needle in ("P0", "severity", "route", "code_refs", "likely_paths", "submit early"):
        assert needle in system
    assert "claim_paths" not in agent_prompt.TRIAGE_INSTRUCTIONS


def test_the_triage_user_turn_carries_open_cards_as_untrusted_data() -> None:
    turn = agent_prompt.build_user_turn(TASK, "T-1", "R-1", lambda: "n0nce", mode="triage")
    data = turn.split('id="n0nce">', 1)[1].rsplit('</untrusted-task-data id="n0nce">', 1)[0]
    assert "Badge missing on cards" in data
    closing = turn.rsplit(">", 1)[1]
    assert "submit_triage" in closing
    assert "acceptance criteria" in closing


def test_a_triage_turn_has_room_for_fifty_open_cards() -> None:
    cards = [{"id": f"{i:036d}", "title": "t" * 200, "column": "backlog"} for i in range(50)]
    task: dict[str, object] = {**TASK, "brief": "b" * 8000, "open_cards": cards}
    assert "submit_triage" in agent_prompt.build_user_turn(task, "T-1", "R-1", mode="triage")
    with pytest.raises(agent_prompt.PromptError):
        agent_prompt.build_user_turn(task, "T-1", "R-1", mode="plan")


def test_the_planner_writes_the_acceptance_criteria() -> None:
    plan = agent_prompt.PLAN_INSTRUCTIONS
    assert "Acceptance criteria" in plan
    assert "## Acceptance criteria" in plan
    assert "Triage did not write acceptance criteria" in plan
    assert "acceptance_criteria" in agent_prompt.WRITER_INSTRUCTIONS


# ------------------------------------------------------------------ start and finish


def test_begin_records_the_open_card_ids(tmp_path: Path) -> None:
    transport = FakeTransport({"/v1/task": (200, TASK)})
    tracker = _tracker()
    log = run_log.RunLog(tmp_path / "out")
    no_proxy = llm_usage.UsageClient("", "")
    turn = ar.begin(_settings(tmp_path), _client(transport), tracker, log, no_proxy)
    assert "submit_triage" in turn.rsplit(">", 1)[1]
    assert tracker.open_cards == frozenset({DUP})


def _finish(tmp_path: Path, submitted: bool) -> tuple[dict[str, object], FakeTransport]:
    transport = FakeTransport()
    tracker = _tracker()
    tracker.triage_submitted = submitted
    spend = llm_usage.Spend(120_000, 500_000, 4, 0)
    outcome = ar.LoopOutcome("end_turn", 4)
    log = run_log.RunLog(tmp_path / "out")
    result = ar.finish(_settings(tmp_path), tracker, outcome, spend, log, _client(transport))
    return result, transport


def test_a_triage_run_never_posts_a_handoff(tmp_path: Path) -> None:
    result, transport = _finish(tmp_path, True)
    assert transport.calls == []
    assert result["triage_submitted"] is True
    assert result["runner_posted_handoff"] is False
    assert result["detail"] == "Triage submitted after 4 turns ($0.12 of $0.50)."


def test_a_triage_run_that_ends_without_submitting_says_so(tmp_path: Path) -> None:
    result, transport = _finish(tmp_path, False)
    assert transport.calls == []
    assert str(result["detail"]).startswith("Stopped: the agent ended without submit_triage")


def test_main_in_triage_mode_stops_before_the_model_without_a_coordinator(
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
    out = tmp_path / "out" / "triage.json"
    argv = [*_argv(tmp_path), "--mode", "triage", "--triage-out", str(out)]
    assert ar.main(argv) == ar.EXIT_COORDINATOR
    assert not out.exists()


# ------------------------------------------------------------------ submit_triage


def _triage_tools(
    tmp_path: Path,
) -> tuple[ct.CoordinatorTools, ap.RunTracker, FakeTransport, Path]:
    repo = tmp_path / "repo"
    (repo / "web/src/features/board").mkdir(parents=True)
    (repo / "web/src/features/board/Card.tsx").write_text("x\n")
    out = tmp_path / "out" / "triage.json"
    transport = FakeTransport()
    tracker = _tracker()
    sink = ct.TriageSink(task_id="T-1", repo=repo, out=out, open_cards=frozenset({DUP}))
    tools = ct.CoordinatorTools(_client(transport), tracker, triage_sink=sink)
    return tools, tracker, transport, out


def _call(tools: ct.CoordinatorTools, name: str, args: dict[str, object]) -> dict[str, object]:
    return asyncio.run(tools.call(name, args))


def _text(result: dict[str, object]) -> str:
    content = cc.as_list(result["content"])
    first = cc.as_object(content[0]) or {}
    return str(first["text"])


def test_triage_specs_are_read_only_plus_submit_triage(tmp_path: Path) -> None:
    tools, _, transport, _ = _triage_tools(tmp_path)
    assert tuple(s.name for s in tools.specs) == ap.TRIAGE_COORDINATOR_TOOLS
    for spec in tools.specs:
        assert spec.input_schema["additionalProperties"] is False
    for name in ("claim_paths", "post_handoff", "submit_plan"):
        assert _call(tools, name, {"paths": ["web/"]})["is_error"] is True
    assert transport.calls == []


def test_submit_triage_writes_the_contract_file(tmp_path: Path) -> None:
    tools, tracker, transport, out = _triage_tools(tmp_path)
    result = _call(tools, "submit_triage", {"triage": TRIAGE})
    assert result["is_error"] is False, _text(result)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["task_id"] == "T-1"
    assert list(written) == list(triage_contract.TOP_KEYS)
    assert tracker.triage_submitted is True
    assert transport.calls == []  # the triage goes to /out, not the coordinator


def test_an_invalid_triage_is_a_fixable_tool_error_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    tools, tracker, _, out = _triage_tools(tmp_path)
    verification = {
        "status": "duplicate",
        "evidence": "Same as another card.",
        "code_refs": ["web/src/features/board/Invented.tsx"],
        "duplicate_of": "00000000-0000-4000-8000-0000000000ff",
    }
    bad: dict[str, object] = {**TRIAGE, "verification": verification, "acceptance_criteria": []}
    result = _call(tools, "submit_triage", {"triage": bad})
    assert result["is_error"] is True
    text = _text(result)
    for needle in ("Invented.tsx", "open cards", "acceptance_criteria", "submit_triage again"):
        assert needle in text
    assert not out.exists()
    assert tracker.triage_submitted is False
    assert _call(tools, "submit_triage", {})["is_error"] is True


def test_a_duplicate_of_a_listed_open_card_is_accepted(tmp_path: Path) -> None:
    tools, _, _, out = _triage_tools(tmp_path)
    verification = {"status": "duplicate", "evidence": "Same change.", "duplicate_of": DUP}
    result = _call(tools, "submit_triage", {"triage": {**TRIAGE, "verification": verification}})
    assert result["is_error"] is False, _text(result)
    assert json.loads(out.read_text(encoding="utf-8"))["verification"]["duplicate_of"] == DUP


# ------------------------------------------------------------------ handoff.json


def _result(tmp_path: Path, submitted: bool) -> Path:
    path = tmp_path / "result.json"
    detail = "Triage submitted after 4 turns ($0.12 of $0.50)." if submitted else "Stopped: x."
    path.write_text(
        json.dumps({"stop": "end_turn", "triage_submitted": submitted, "detail": detail})
    )
    return path


def test_triage_handoff_is_done_with_a_valid_triage(tmp_path: Path) -> None:
    path = tmp_path / "triage.json"
    triage_contract.write(triage_contract.validate(TRIAGE, "T-1"), path)
    h = handoff.build_triage("T-1", path, handoff.read_agent_result(_result(tmp_path, True)), [])
    assert h["status"] == "done"
    summary = str(h["summary"])
    assert "bug, P2/minor, route next, confirmed" in summary
    assert "Maintainers" not in summary  # free text stays out of the summary
    assert h["changed_paths"] == []


@pytest.mark.parametrize("content", [None, "not json", json.dumps({**TRIAGE, "route": "soon"})])
def test_triage_handoff_fails_without_a_valid_triage(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "triage.json"
    if content is not None:
        path.write_text(content)
    h = handoff.build_triage("T-1", path, handoff.read_agent_result(_result(tmp_path, False)), [])
    assert h["status"] == "failed"
    assert str(h["summary"]).startswith("Stopped: x. No valid triage was written.")


def test_triage_handoff_cli(tmp_path: Path) -> None:
    path = tmp_path / "triage.json"
    triage_contract.write(triage_contract.validate(TRIAGE, "T-1"), path)
    out = tmp_path / "handoff.json"
    args = ["--task-id", "T-1", "--out", str(out), "--triage-mode", "--triage-json", str(path)]
    assert handoff.main([*args, "--agent-result", str(_result(tmp_path, True))]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "done"
    with pytest.raises(SystemExit):
        handoff.main(["--task-id", "T-1", "--out", str(out), "--triage-mode"])
