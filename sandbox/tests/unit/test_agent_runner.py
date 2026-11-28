"""Runner helpers that don't need the SDK (it exists only in the image venv)."""

from __future__ import annotations

import json
from pathlib import Path

import agent_policy as ap
import agent_runner as ar
import pytest

ENV = {
    "TASK_ID": "T-1",
    "RUN_ID": "R-1",
    "COORDINATOR_URL": "http://coordinator:8100",
    "COORDINATOR_TOKEN": "jrc1.fake.fake",
    "ANTHROPIC_BASE_URL": "http://llm-proxy:8080",
    "ANTHROPIC_AUTH_TOKEN": "jrt2.fake.fake",
}
ARGV = ["--base-dir", "/sandbox/agent/base", "--out", "/sandbox/agent/result.json"]


def test_settings_defaults_and_bounds() -> None:
    s = ar.load_settings(ENV, ARGV)
    # The writer runs on Sonnet (the human's choice); plans stay on Opus.
    assert (s.model, s.max_turns, s.token_budget) == ("claude-sonnet-5-5", 60, 3_000_000)
    s = ar.load_settings({**ENV, "AGENT_MAX_TURNS": "12", "AGENT_TOKEN_BUDGET": "50000"}, ARGV)
    assert (s.max_turns, s.token_budget) == (12, 50_000)
    for bad in ({"AGENT_MAX_TURNS": "0"}, {"AGENT_MAX_TURNS": "x"}, {"AGENT_TOKEN_BUDGET": "1"}):
        with pytest.raises(ar.SettingsError):
            ar.load_settings({**ENV, **bad}, ARGV)


@pytest.mark.parametrize("name", ["TASK_ID", "COORDINATOR_URL", "COORDINATOR_TOKEN"])
def test_settings_require_contract_env(name: str) -> None:
    env = {k: v for k, v in ENV.items() if k != name}
    with pytest.raises(ar.SettingsError) as info:
        ar.load_settings(env, ARGV)
    assert name in str(info.value)
    assert "jrc1" not in str(info.value) and "jrt2" not in str(info.value)


def test_options_no_web_no_repo_settings_no_coordinator_config() -> None:
    s = ar.load_settings(ENV, ARGV)
    opts = ar.option_kwargs(s, "SYSTEM", ENV)
    assert opts["tools"] == ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    assert opts["allowed_tools"] == []
    assert {"WebFetch", "WebSearch", "Task"} <= set(opts["disallowed_tools"])
    # ADR-0009: no MCP config pointing at the coordinator (agent_sdk adds the in-process tools).
    assert "mcp_servers" not in opts
    assert opts["strict_mcp_config"] is True
    assert opts["setting_sources"] == []
    assert opts["model"] == "claude-sonnet-5-5" and opts["max_turns"] == 60
    assert opts["system_prompt"] == "SYSTEM"
    assert opts["cwd"] == ap.WORKSPACE
    text = json.dumps(opts)
    assert "jrc1.fake.fake" not in text and "coordinator:8100" not in text
    assert "coordinator" in opts["env"]["NO_PROXY"]


def test_check_tool_counts_denials_and_follows_leases() -> None:
    tracker = ap.RunTracker(token_budget=10)
    assert not ar.check_tool(tracker, "Bash", {"command": "curl x"}).allowed
    assert not ar.check_tool(tracker, "Edit", {"file_path": "web/src/a.ts"}).allowed
    assert tracker.denials == 2
    assert ar.check_tool(tracker, "mcp__coordinator__claim_paths", {"paths": ["web/"]}).allowed
    tracker.add_leases(["web/"])
    assert ar.check_tool(tracker, "Edit", {"file_path": "web/src/a.ts"}).allowed
    assert tracker.denials == 2
    out = ar.pre_tool_use_output(ar.check_tool(tracker, "WebFetch", {}))
    hook = out["hookSpecificOutput"]
    assert isinstance(hook, dict) and hook["permissionDecision"] == "deny"
    assert ar.pre_tool_use_output(ap.ALLOW) == {}


def test_stop_reason() -> None:
    assert ar.stop_reason("success", False) == "end_turn"
    assert ar.stop_reason("error_max_turns", True) == "max_turns"
    assert ar.stop_reason("error_during_execution", True) == "error"


def test_setup_failure_writes_result_without_secrets(tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    argv = ["--base-dir", str(tmp_path / "missing"), "--out", str(out)]
    env = {**ENV, "COORDINATOR_URL": "http://127.0.0.1:9"}
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env.items():
            mp.setenv(k, v)
        assert ar.main(argv) == ar.EXIT_CONFIG
    text = out.read_text()
    data = json.loads(text)
    assert data["stop"] == "setup_error" and data["posted_handoff"] is False
    assert "jrc1" not in text and "jrt2" not in text
