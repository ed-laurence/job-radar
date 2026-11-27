"""Writer agent entry point: settings, prompt, task fetch, result file. SDK glue: agent_sdk.py.

Run by entrypoint.sh (AGENT_MODE=agent) after checkout, with the image's agent venv:

    /opt/agent/venv/bin/python /opt/sandbox/agent_runner.py \\
        --repo /workspace/repo --base-dir /tmp/agent/base --out /tmp/agent/result.json

1. The task is fetched here over the coordinator HTTP API (``GET /v1/task``, all pages), so
   its text lands in a nonce-delimited data block of the user turn, never the system prompt.
2. The system prompt is the writer instructions + AGENTS.md + docs/standards/ from the BASE
   commit (``--base-dir``, filled by the entrypoint with ``git archive``).
3. Tools: Read/Glob/Grep/Edit/Write/Bash gated by ``agent_policy``, plus the coordinator tools
   (``coordinator_tools``) as in-process SDK tools: no MCP config, no network MCP server
   (ADR-0009). No web tools, no repo settings.
4. Budgets: ``AGENT_MAX_TURNS`` (SDK) and ``AGENT_TOKEN_BUDGET`` (counted like llm-proxy; the
   run is interrupted when it is spent). llm-proxy enforces the token budget as well.
5. ``--out`` receives a small JSON result (stop reason, counts, the post_handoff input) that
   handoff.py merges. No transcript or tool output is written anywhere the host can read.

``COORDINATOR_TOKEN`` stays in this process: it is removed from ``os.environ`` before the CLI
starts, so neither the CLI nor any tool subprocess sees it.
This module has no SDK import (agent_sdk is imported in ``main``), so it is unit-tested on the
host without the SDK.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import agent_policy
import agent_prompt
import coordinator_client

DEFAULT_MODEL = "claude-opus-5-5"
SMALL_MODEL = "claude-haiku-5-5"
CLI_PATH = "/usr/local/bin/claude"
EXIT_CONFIG = 3


@dataclass(frozen=True)
class Settings:
    task_id: str
    run_id: str
    coordinator_url: str
    coordinator_token: str
    model: str
    max_turns: int
    token_budget: int
    repo: Path
    base_dir: Path
    out: Path


class SettingsError(Exception):
    pass


def _bounded_int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer") from None
    if not lo <= value <= hi:
        raise SettingsError(f"{name} must be between {lo} and {hi}")
    return value


def load_settings(env: Mapping[str, str], argv: list[str] | None = None) -> Settings:
    parser = argparse.ArgumentParser(prog="agent_runner")
    parser.add_argument("--repo", type=Path, default=Path(agent_policy.WORKSPACE))
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    missing = [
        n
        for n in ("TASK_ID", "COORDINATOR_URL", "COORDINATOR_TOKEN", "ANTHROPIC_BASE_URL")
        if not env.get(n)
    ]
    if not env.get("ANTHROPIC_AUTH_TOKEN"):
        missing.append("ANTHROPIC_AUTH_TOKEN")
    if missing:
        raise SettingsError(f"missing env: {', '.join(missing)}")
    return Settings(
        task_id=env["TASK_ID"],
        run_id=env.get("RUN_ID", ""),
        coordinator_url=env["COORDINATOR_URL"],
        coordinator_token=env["COORDINATOR_TOKEN"],
        model=env.get("AGENT_MODEL") or DEFAULT_MODEL,
        max_turns=_bounded_int(env, "AGENT_MAX_TURNS", 60, 1, 500),
        token_budget=_bounded_int(env, "AGENT_TOKEN_BUDGET", 3_000_000, 1_000, 50_000_000),
        repo=args.repo,
        base_dir=args.base_dir,
        out=args.out,
    )


# ------------------------------------------------------------------ pure option/hook helpers


def cli_env(env: Mapping[str, str]) -> dict[str, str]:
    """Extra env for the CLI (merged over os.environ, which no longer has the coordinator
    token)."""
    no_proxy = "localhost,127.0.0.1,llm-proxy,coordinator"
    return {
        "ANTHROPIC_SMALL_FAST_MODEL": SMALL_MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": SMALL_MODEL,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
        "NO_PROXY": env.get("NO_PROXY") or no_proxy,
        "no_proxy": env.get("no_proxy") or no_proxy,
    }


def option_kwargs(settings: Settings, system_prompt: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Everything passed to ``ClaudeAgentOptions`` except the callbacks and the in-process
    coordinator tool server (added by agent_sdk)."""
    return {
        "tools": list(agent_policy.BASE_TOOLS),
        "allowed_tools": [],  # nothing pre-approved: every call goes through can_use_tool
        "disallowed_tools": list(agent_policy.DENIED_TOOLS),
        "system_prompt": system_prompt,
        "strict_mcp_config": True,  # only the in-process server agent_sdk passes; no .mcp.json
        "setting_sources": [],  # no user/project settings, hooks or CLAUDE.md from the repo
        "permission_mode": "default",
        "model": settings.model,
        "max_turns": settings.max_turns,
        "cwd": settings.repo.as_posix(),
        "cli_path": CLI_PATH,
        "env": cli_env(env),
    }


def pre_tool_use_output(decision: agent_policy.Decision) -> dict[str, object]:
    if decision.allowed:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }
    }


def check_tool(
    tracker: agent_policy.RunTracker, name: str, tool_input: Mapping[str, object]
) -> agent_policy.Decision:
    """The one policy check used by the PreToolUse hook and by can_use_tool."""
    decision = agent_policy.tool_allowed(name, tool_input, tracker.leases)
    if not decision.allowed:
        tracker.denials += 1
    return decision


def stop_reason(subtype: str, is_error: bool) -> str:
    if subtype == "success" and not is_error:
        return "end_turn"
    if subtype == "error_max_turns":
        return "max_turns"
    return "error"


def write_result(path: Path, result: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def say(msg: str) -> None:
    print(f"[agent] {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    env = dict(os.environ)
    try:
        settings = load_settings(env, argv)
    except SettingsError as exc:
        say(str(exc))
        return EXIT_CONFIG
    # Only this process talks to the coordinator; the CLI and its tool subprocesses inherit
    # os.environ, so the token leaves it now.
    os.environ.pop("COORDINATOR_TOKEN", None)
    env.pop("COORDINATOR_TOKEN", None)
    tracker = agent_policy.RunTracker(token_budget=settings.token_budget)
    try:
        system_prompt = agent_prompt.build_system_prompt(
            agent_prompt.read_base_files(settings.base_dir)
        )
        client = coordinator_client.CoordinatorClient(
            settings.coordinator_url, settings.coordinator_token
        )
        task = client.get_task()
        user_turn = agent_prompt.build_user_turn(task, settings.task_id, settings.run_id)
    except (agent_prompt.PromptError, coordinator_client.CoordinatorError, OSError) as exc:
        say(f"setup failed: {exc}")
        write_result(settings.out, {**tracker.result("setup_error", 0), "error": str(exc)[:160]})
        return EXIT_CONFIG
    say(f"model={settings.model} max_turns={settings.max_turns} budget={settings.token_budget}")
    import agent_sdk  # the SDK exists only in the image venv

    try:
        stop, turns = asyncio.run(
            agent_sdk.run_agent(settings, system_prompt, user_turn, tracker, client, env)
        )
    except Exception as exc:  # the SDK raises several unrelated types; record and report
        say(f"agent loop failed: {type(exc).__name__}")
        stop, turns = "error", 0
    write_result(settings.out, tracker.result(stop, turns))
    say(
        f"stop={stop} turns={turns} tokens={tracker.tokens_used} steps={tracker.steps_posted} "
        f"handoff={'yes' if tracker.handoff is not None else 'no'} denied={tracker.denials}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
