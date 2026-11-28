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

Plan runs (``--mode plan``, AGENT_MODE=plan; the read-only dev-lead planner): the plan
instructions, read-only tools (``agent_policy.PLAN_TOOLS``) and the plan coordinator tools
(get_task, post_step, request_help, submit_plan). ``submit_plan`` writes ``--plan-out``
(``/out/plan.json``); the runner never posts a handoff for a plan run, the entrypoint writes
``/out/handoff.json`` from the result.

Triage runs (``--mode triage``, AGENT_MODE=triage; the read-only triager): the triage
instructions, plan mode's read-only tools plus Bash for existing tests (``agent_policy``,
``--base-files`` lists them) and the triage coordinator tools (get_task, post_step,
request_help, submit_triage). ``submit_triage`` writes ``--triage-out`` (``/out/triage.json``);
``duplicate_of`` must be one of the ``open_cards`` get_task listed. Like a plan run, no
handoff is posted. The model defaults per mode (``DEFAULT_MODELS``: triage runs on Sonnet);
``AGENT_MODEL_<MODE>`` (e.g. ``AGENT_MODEL_TRIAGE``) then ``AGENT_MODEL`` override it.

Review runs (``--mode review``, AGENT_MODE=review; the read-only reviewer of a published
branch): the review instructions, plan mode's read-only tools (``git diff <base>..HEAD``
included) and the review coordinator tools (get_task, post_step, request_help,
submit_review). ``submit_review`` writes ``--review-out`` (``/out/review.json``); every
finding's path must be one of ``--changed-files`` (``git diff --name-only -z <base>..HEAD``,
written by the entrypoint). No handoff is posted. The model defaults to Opus.

Dev runs get ``--base-files`` (``git ls-files -z`` at the base commit, written by the
entrypoint): Edit/Write on those existing test files needs a ``modify-tests:`` label on the
card, read from ``get_task``.

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

# Writer and reviewer on Sonnet, the planner on Opus, triage on Sonnet (the human's choice).
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MODELS = {
    "dev": DEFAULT_MODEL,
    "plan": "claude-opus-5-5",
    "triage": "claude-sonnet-5-5",
    "review": "claude-sonnet-5-5",
}
# The tool that ends each read-only run, and what the board says once it was accepted.
FINAL_TOOLS = {"plan": "submit_plan", "triage": "submit_triage", "review": "submit_review"}
SUBMITTED = {"plan": "Plan submitted", "triage": "Triage submitted", "review": "Review submitted"}
# Run modes (AGENT_MODE / --mode) and the prompt each one uses.
RUN_MODES = ("dev", "plan", "triage", "review")
PROMPT_MODES = {"review": "reviewer"}
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
    mode: str = "dev"
    plan_out: Path | None = None
    base_files: Path | None = None
    triage_out: Path | None = None
    review_out: Path | None = None
    changed_files: Path | None = None

    @property
    def is_plan(self) -> bool:
        return self.mode == "plan"

    @property
    def is_triage(self) -> bool:
        return self.mode == "triage"

    @property
    def is_review(self) -> bool:
        return self.mode == "review"

    @property
    def prompt_mode(self) -> str:
        """The ``agent_prompt`` mode: the reviewer's prompt is named after its role."""
        return PROMPT_MODES.get(self.mode, self.mode)

    @property
    def is_read_only(self) -> bool:
        """Plan, triage and review runs: no edits, no leases, no handoff on the coordinator."""
        return self.is_plan or self.is_triage or self.is_review


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
    parser.add_argument("--mode", choices=RUN_MODES, default="dev")
    parser.add_argument("--plan-out", type=Path, help="plan mode: where submit_plan writes")
    parser.add_argument("--triage-out", type=Path, help="triage mode: where submit_triage writes")
    parser.add_argument("--base-files", type=Path, help="git ls-files -z at the base")
    parser.add_argument("--review-out", type=Path, help="review mode: where submit_review writes")
    parser.add_argument(
        "--changed-files", type=Path, help="review mode: git diff --name-only -z base..HEAD"
    )
    args = parser.parse_args(argv)
    if args.mode == "plan" and args.plan_out is None:
        raise SettingsError("--mode plan needs --plan-out")
    if args.mode == "triage" and args.triage_out is None:
        raise SettingsError("--mode triage needs --triage-out")
    if args.mode == "review" and (args.review_out is None or args.changed_files is None):
        raise SettingsError("--mode review needs --review-out and --changed-files")
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
        model=model_for(args.mode, env),
        max_turns=_bounded_int(env, "AGENT_MAX_TURNS", 60, 1, 500),
        token_budget=_bounded_int(env, "AGENT_TOKEN_BUDGET", 3_000_000, 1_000, 50_000_000),
        repo=args.repo,
        base_dir=args.base_dir,
        out=args.out,
        log_dir=args.log_dir or args.out.parent,
        timeout_s=_bounded_int(env, "AGENT_TIMEOUT_S", 0, 0, 99_999) or None,
        mode=args.mode,
        plan_out=args.plan_out,
        base_files=args.base_files,
        triage_out=args.triage_out,
        review_out=args.review_out,
        changed_files=args.changed_files,
    )


def model_for(mode: str, env: Mapping[str, str]) -> str:
    """``AGENT_MODEL_<MODE>``, then ``AGENT_MODEL``, then the mode's default."""
    return (
        env.get(f"AGENT_MODEL_{mode.upper()}", "").strip()
        or env.get("AGENT_MODEL", "").strip()
        or DEFAULT_MODELS.get(mode, DEFAULT_MODEL)
    )


def existing_test_files(listing: Path) -> frozenset[str]:
    """Test files tracked at the base commit, from the entrypoint's ``git ls-files -z``."""
    names = listing.read_bytes().decode("utf-8", errors="replace").split("\0")
    return frozenset(n for n in names if n and agent_policy.is_test_path(n))


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
    tools = agent_policy.PLAN_TOOLS if settings.is_read_only else agent_policy.BASE_TOOLS
    denied = agent_policy.DENIED_TOOLS
    if settings.is_read_only:
        denied = (*denied, *agent_policy.FILE_WRITE_TOOLS)
    return {
        "tools": list(tools),
        "allowed_tools": [],  # nothing pre-approved: every call goes through can_use_tool
        "disallowed_tools": list(denied),
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
    decision = agent_policy.tool_allowed(
        name,
        tool_input,
        tracker.leases,
        plan=tracker.plan_mode,
        triage=tracker.triage_mode,
        review=tracker.review_mode,
        existing_tests=tracker.existing_tests,
        modify_tests=tracker.modify_tests,
    )
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


def task_labels(task: Mapping[str, object]) -> list[object]:
    """The card's labels from a ``get_task`` result (``task.labels``)."""
    card = coordinator_client.as_object(task.get("task")) or {}
    return coordinator_client.as_list(card.get("labels"))


def open_card_ids(task: Mapping[str, object]) -> frozenset[str]:
    """The ids of the open cards a triage run's ``get_task`` listed."""
    cards = (
        coordinator_client.as_object(c) for c in coordinator_client.as_list(task.get("open_cards"))
    )
    return frozenset(
        str(card["id"]) for card in cards if card is not None and isinstance(card.get("id"), str)
    )


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
    user_turn = agent_prompt.build_user_turn(
        task, settings.task_id, settings.run_id, mode=settings.prompt_mode
    )
    tracker.modify_tests = agent_policy.parse_modify_tests(task_labels(task))
    if settings.is_triage:
        tracker.open_cards = open_card_ids(task)
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
    """One plain-words sentence for the handoff summary and the board. In plan (triage) mode
    ``handoff_posted`` means an accepted submit_plan (submit_triage)."""
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
            return f"{SUBMITTED.get(settings.mode, 'Agent finished')} {after}."
        final = FINAL_TOOLS.get(settings.mode, "post_handoff")
        return f"Stopped: the agent ended without {final} {after}."
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
    """Write the result for handoff.py; if a dev agent posted no handoff, post a failed one
    (plan and triage runs have no handoff on the coordinator: the entrypoint writes
    handoff.json)."""
    stop = classify_stop(outcome, spend)
    turns = outcome.turns or log.turns
    if settings.is_review:
        done = tracker.review_submitted
    elif settings.is_read_only:
        done = tracker.triage_submitted if settings.is_triage else tracker.plan_submitted
    else:
        done = tracker.handoff is not None
    detail = stop_detail(stop, turns, settings, spend, outcome.error, done)
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
    if tracker.handoff is None and client is not None and not settings.is_read_only:
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
    tracker = agent_policy.RunTracker(
        token_budget=settings.token_budget,
        plan_mode=settings.is_plan,
        triage_mode=settings.is_triage,
        review_mode=settings.is_review,
    )
    log = run_log.RunLog(settings.log_dir)
    spend_client = llm_usage.UsageClient(
        env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_AUTH_TOKEN", "")
    )
    try:
        system_prompt = agent_prompt.build_system_prompt(
            agent_prompt.read_base_files(settings.base_dir), mode=settings.prompt_mode
        )
        if settings.base_files is not None and not (settings.is_plan or settings.is_review):
            tracker.existing_tests = existing_test_files(settings.base_files)
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
