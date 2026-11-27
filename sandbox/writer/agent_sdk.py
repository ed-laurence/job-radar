"""Claude Agent SDK glue for the writer (imported by agent_runner.main inside the image).

The coordinator tools are in-process SDK tools (``create_sdk_mcp_server``): calls go from the
CLI back to this process over the SDK control channel, and this process calls the coordinator
HTTP API. There is no network MCP server and no MCP config pointing at the coordinator.

After each model turn the runner reads the task's spend from llm-proxy (``GET /v1/usage``),
appends a metadata line to ``agent.log`` and interrupts the run once the budget is used up.
SIGTERM (the entrypoint's wall-clock ``timeout``) cancels the loop and reports ``timeout``.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

import agent_policy
import agent_runner
import coordinator_client
import coordinator_tools
import llm_usage
import run_log
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    ToolPermissionContext,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import McpSdkServerConfig


def coordinator_server(tools: coordinator_tools.CoordinatorTools) -> McpSdkServerConfig:
    def handler(name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            return await tools.call(name, args)

        return run

    return create_sdk_mcp_server(
        name="coordinator",
        tools=[
            SdkMcpTool(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                handler=handler(spec.name),
            )
            for spec in coordinator_tools.SPECS
        ],
    )


def _deny_output(reason: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


async def run_agent(
    settings: agent_runner.Settings,
    system_prompt: str,
    user_turn: str,
    tracker: agent_policy.RunTracker,
    client: coordinator_client.CoordinatorClient,
    env: Mapping[str, str],
    log: run_log.RunLog,
    spend_client: llm_usage.UsageClient,
) -> agent_runner.LoopOutcome:
    tools = coordinator_tools.CoordinatorTools(client, tracker, on_step=log.step)

    async def pre_tool_use(
        data: HookInput, _tool_use_id: str | None, _ctx: HookContext
    ) -> HookJSONOutput:
        payload = cast("Mapping[str, object]", data)
        name = payload.get("tool_name")
        tool_input = coordinator_client.as_object(payload.get("tool_input")) or {}
        decision = agent_runner.check_tool(
            tracker, name if isinstance(name, str) else "", tool_input
        )
        return {} if decision.allowed else _deny_output(decision.reason)

    async def can_use_tool(
        name: str, tool_input: dict[str, Any], _ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        decision = agent_runner.check_tool(tracker, name, tool_input)
        if decision.allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message=decision.reason, interrupt=False)

    turns = agent_runner.TurnBuffer()

    async def log_turn(done: tuple[list[str], Mapping[str, object] | None] | None) -> bool:
        """Log a finished turn with the spend so far; True when the budget is used up."""
        if done is None:
            return False
        spend = await asyncio.to_thread(spend_client.fetch)
        log.turn(done[0], done[1], spend.used if spend else None, spend.budget if spend else None)
        return spend is not None and spend.exhausted

    outcome = agent_runner.LoopOutcome("error")
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    timed_out = False

    def on_sigterm() -> None:
        nonlocal timed_out
        timed_out = True
        if task is not None:
            task.cancel()

    loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    log_path: Path = settings.out.parent / "cli.log"
    try:
        with log_path.open("a", encoding="utf-8") as cli_log:

            def on_stderr(line: str) -> None:
                cli_log.write(line.rstrip() + "\n")

            options = ClaudeAgentOptions(
                **agent_runner.option_kwargs(settings, system_prompt, env),
                mcp_servers={"coordinator": coordinator_server(tools)},
                can_use_tool=can_use_tool,
                hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])]},
                stderr=on_stderr,
            )
            async with ClaudeSDKClient(options=options) as sdk:
                await sdk.query(user_turn)
                async for message in sdk.receive_response():
                    if isinstance(message, AssistantMessage):
                        tracker.on_usage(message.message_id, message.usage)
                        names = [b.name for b in message.content if isinstance(b, ToolUseBlock)]
                        spent = await log_turn(turns.add(message.message_id, names, message.usage))
                        if tracker.over_budget or spent:
                            outcome.stop = "budget"
                            await sdk.interrupt()
                            break
                    elif isinstance(message, ResultMessage):
                        outcome.turns = message.num_turns
                        outcome.stop = agent_runner.stop_reason(message.subtype, message.is_error)
                        outcome.api_error_status = message.api_error_status
                        outcome.result_text = (message.result or "")[:500]
                await log_turn(turns.take())
    except asyncio.CancelledError:
        if not timed_out:
            raise
        if task is not None:
            task.uncancel()
        outcome.stop = "timeout"
        await log_turn(turns.take())
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
    return outcome
