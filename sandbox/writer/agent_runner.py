"""Writer agent entry point: settings, prompt, task fetch, result file. SDK glue: agent_sdk.py.

Run by entrypoint.sh (AGENT_MODE=agent) after checkout, with the image's agent venv:

    /opt/agent/venv/bin/python /opt/sandbox/agent_runner.py \\
        --repo /workspace/repo --base-dir /tmp/agent/base --out /tmp/agent/result.json

1. Before any model call, code (not the model) fetches the task over the coordinator HTTP API
   (``GET /v1/task``, all pages) and posts a "started" step. If either fails (network, 401,
   403...), the run stops with ``coordinator_unreachable`` and exit code 4, having spent nothing
   on the model. The task text lands in a nonce-delimited data block of the user turn, never
   the system prompt.
2. The system prompt is the writer instructions + AGENTS.md + docs/standards/ from the BASE
   commit (``--base-dir``, filled by the entrypoint with ``git archive``).
3. Tools: Read/Glob/Grep/Edit/Write/Bash gated by ``agent_policy``, plus the coordinator tools
   (``coordinator_tools``) as in-process SDK tools: no MCP config, no network MCP server
   (ADR-0009). No web tools, no repo settings.
4. Budgets: ``AGENT_MAX_TURNS`` (SDK), ``AGENT_TOKEN_BUDGET`` (raw token count, a backstop)
   and the task's USD budget, which llm-proxy enforces; after every turn the runner reads the
   spend from llm-proxy ``GET /v1/usage`` and interrupts the run once the budget is used up.
5. Every stop (end of turn, budget, max turns, SIGTERM from the wall-clock timeout, exception)
   ends in ``finish``: ``--out`` receives a small JSON result (stop reason, a plain-words
   ``detail`` such as "Stopped: model budget used up after 4 turns ($1.97 of $2.00).", counts,
   spend, the post_handoff input) that handoff.py merges, and if the agent never posted a
   handoff the runner posts a failed one itself (best effort). ``--log-dir`` (``/out``)
   receives ``agent.log`` and ``steps.jsonl`` (run_log.py); no transcript, prompt or tool
   output is written anywhere the host can read.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import agent_policy
import agent_prompt
import coordinator_client
import llm_usage
import run_log

DEFAULT_MODEL = "claude-opus-5-5"
SMALL_MODEL = "claude-haiku-5-5"
CLI_PATH = "/usr/local/bin/claude"
EXIT_CONFIG = 3
EXIT_COORDINATOR = 4
FINAL_POST_TIMEOUT_S = 5.0


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
    log_dir: Path
    timeout_s: int | None = None


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
    parser.add_argument("--log-dir", type=Path, help="agent.log + steps.jsonl (default: --out dir)")
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
        log_dir=args.log_dir or args.out.parent,
        timeout_s=_bounded_int(env, "AGENT_TIMEOUT_S", 0, 0, 99_999) or None,
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


# ------------------------------------------------------------------ start: task first, by code


class CoordinatorUnreachableError(Exception):
    """get_task or the "started" step failed: the run must not reach the model."""

    def __init__(self, status: str) -> None:
        super().__init__(f"coordinator unreachable: {status}")
        self.reason = f"coordinator unreachable: {status}"


def coordinator_status(exc: coordinator_client.CoordinatorError) -> str:
    """The HTTP status, or the transport error class (never a URL or token)."""
    if exc.status is not None:
        return str(exc.status)
    text, prefix = str(exc), "coordinator unreachable: "
    return (text[len(prefix) :] if text.startswith(prefix) else text)[:120]


def post_step(
    client: coordinator_client.CoordinatorClient,
    tracker: agent_policy.RunTracker,
    log: run_log.RunLog,
    kind: str,
    text: str,
    timeout: float | None = None,
) -> None:
    client.step(kind, text, timeout=timeout)
    tracker.steps_posted += 1
    log.step(kind, text)


def begin(
    settings: Settings,
    client: coordinator_client.CoordinatorClient,
    tracker: agent_policy.RunTracker,
    log: run_log.RunLog,
    spend_client: llm_usage.UsageClient,
) -> str:
    """get_task, then the "started" step. Returns the user turn.

    Raises CoordinatorUnreachableError (nothing spent) or agent_prompt.PromptError.
    """
    try:
        task = client.get_task()
    except coordinator_client.CoordinatorError as exc:
        raise CoordinatorUnreachableError(coordinator_status(exc)) from None
    user_turn = agent_prompt.build_user_turn(task, settings.task_id, settings.run_id)
    spend = spend_client.fetch()  # a free read of the token's budget, not a model call
    budget = f", budget {llm_usage.usd(spend.budget)}" if spend else ""
    text = f"Started: model {settings.model}, up to {settings.max_turns} turns{budget}."
    try:
        post_step(client, tracker, log, "message", text)
    except coordinator_client.CoordinatorError as exc:
        raise CoordinatorUnreachableError(coordinator_status(exc)) from None
    return user_turn


# ------------------------------------------------------------------ the loop's outcome


@dataclass
class LoopOutcome:
    """What agent_sdk.run_agent reports (no message content)."""

    stop: str
    turns: int = 0
    api_error_status: int | None = None
    result_text: str | None = None
    error: str | None = None


@dataclass
class TurnBuffer:
    """One API response can arrive as several AssistantMessages with the same id: gather them,
    hand back a finished turn when the next id starts (or at the end via ``take``)."""

    message_id: str | None = None
    tools: list[str] = field(default_factory=list[str])
    usage: Mapping[str, object] | None = None
    active: bool = False

    def add(
        self, message_id: str | None, tools: Sequence[str], usage: Mapping[str, object] | None
    ) -> tuple[list[str], Mapping[str, object] | None] | None:
        done = None
        if self.active and (message_id is None or message_id != self.message_id):
            done = self.take()
        self.active, self.message_id = True, message_id
        self.tools.extend(tools)
        if usage is not None:
            self.usage = usage
        return done

    def take(self) -> tuple[list[str], Mapping[str, object] | None] | None:
        if not self.active:
            return None
        done = (self.tools, self.usage)
        self.tools, self.usage, self.active, self.message_id = [], None, False, None
        return done


def classify_stop(outcome: LoopOutcome, spend: llm_usage.Spend | None) -> str:
    """A model call refused by llm-proxy for budget ends the CLI with an API error: say so."""
    if outcome.stop != "error":
        return outcome.stop
    refused = spend is not None and spend.refusals > 0
    text = (outcome.result_text or "").lower()
    if refused or (outcome.api_error_status == 403 and "budget" in text):
        return "budget"
    return "error"


def _turns(n: int) -> str:
    return f"{n} turn" + ("" if n == 1 else "s")


def stop_detail(
    stop: str,
    turns: int,
    settings: Settings,
    spend: llm_usage.Spend | None,
    error: str | None = None,
    handoff_posted: bool = False,
) -> str:
    """One plain-words sentence for the handoff summary and the board."""
    money = (
        f"{llm_usage.usd(spend.used)} of {llm_usage.usd(spend.budget)}"
        if spend
        else "spend unknown"
    )
    after = f"after {_turns(turns)} ({money})"
    if stop == "budget":
        return f"Stopped: model budget used up {after}."
    if stop == "max_turns":
        return f"Stopped: turn limit of {settings.max_turns} reached {after}."
    if stop == "timeout":
        limit = f" of {settings.timeout_s}s" if settings.timeout_s else ""
        return f"Stopped: wall-clock limit{limit} reached {after}."
    if stop == "end_turn":
        if handoff_posted:
            return f"Agent finished {after}."
        return f"Stopped: the agent ended without post_handoff {after}."
    cause = f" ({error})" if error else ""
    return f"Stopped: the agent loop failed{cause} {after}."


def failed_handoff(detail: str) -> dict[str, object]:
    """The runner's stand-in when the agent posted none. Changed paths and test counts come
    from the sandbox afterwards (handoff.json), never from here."""
    return {
        "status": "failed",
        "summary": detail,
        "changed_paths": [],
        "tests": {"passed": 0, "failed": 0, "skipped": 0, "first_failing": None},
        "rubric_checked": [],
        "open_questions": [],
        "skipped_checks": ["tests and changed paths: in the sandbox handoff, after the agent"],
    }


def finish(
    settings: Settings,
    tracker: agent_policy.RunTracker,
    outcome: LoopOutcome,
    spend: llm_usage.Spend | None,
    log: run_log.RunLog,
    client: coordinator_client.CoordinatorClient | None,
) -> dict[str, object]:
    """Write the result for handoff.py; if the agent posted no handoff, post a failed one."""
    stop = classify_stop(outcome, spend)
    turns = outcome.turns or log.turns
    detail = stop_detail(stop, turns, settings, spend, outcome.error, tracker.handoff is not None)
    result: dict[str, object] = {
        **tracker.result(stop, turns),
        "detail": detail,
        "spend": spend.as_dict() if spend else None,
        "runner_posted_handoff": False,
    }
    write_result(settings.out, result)  # first, in case the final post hangs until SIGKILL
    log.event(
        "stop",
        stop=stop,
        turns=turns,
        usd_micro_used=spend.used if spend else None,
        usd_micro_budget=spend.budget if spend else None,
    )
    if tracker.handoff is None and client is not None:
        try:
            client.handoff(failed_handoff(detail), timeout=FINAL_POST_TIMEOUT_S)
            result["runner_posted_handoff"] = True
            write_result(settings.out, result)
        except coordinator_client.CoordinatorError as exc:
            say(f"final handoff not posted: {coordinator_status(exc)}")
    return result


# ------------------------------------------------------------------ entry point


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
    log = run_log.RunLog(settings.log_dir)
    spend_client = llm_usage.UsageClient(
        env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_AUTH_TOKEN", "")
    )
    try:
        system_prompt = agent_prompt.build_system_prompt(
            agent_prompt.read_base_files(settings.base_dir)
        )
        client = coordinator_client.CoordinatorClient(
            settings.coordinator_url, settings.coordinator_token
        )
        user_turn = begin(settings, client, tracker, log, spend_client)
    except CoordinatorUnreachableError as exc:
        say(exc.reason)
        detail = f"Stopped: {exc.reason}. The model was not called."
        write_result(
            settings.out,
            {**tracker.result("coordinator_unreachable", 0), "detail": detail, "error": exc.reason},
        )
        log.event("stop", stop="coordinator_unreachable", turns=0, reason=exc.reason)
        return EXIT_COORDINATOR
    except (agent_prompt.PromptError, coordinator_client.CoordinatorError, OSError) as exc:
        say(f"setup failed: {exc}")
        write_result(settings.out, {**tracker.result("setup_error", 0), "error": str(exc)[:160]})
        log.event("stop", stop="setup_error", turns=0)
        return EXIT_CONFIG
    say(f"model={settings.model} max_turns={settings.max_turns} budget={settings.token_budget}")
    import agent_sdk  # the SDK exists only in the image venv

    try:
        outcome = asyncio.run(
            agent_sdk.run_agent(
                settings, system_prompt, user_turn, tracker, client, env, log, spend_client
            )
        )
    except Exception as exc:  # the SDK raises several unrelated types; record and report
        say(f"agent loop failed: {type(exc).__name__}")
        outcome = LoopOutcome("error", error=type(exc).__name__)
    result = finish(settings, tracker, outcome, spend_client.fetch(), log, client)
    say(
        f"stop={result['stop']} turns={result['turns']} tokens={tracker.tokens_used} "
        f"steps={tracker.steps_posted} handoff={'yes' if tracker.handoff is not None else 'no'} "
        f"denied={tracker.denials}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
