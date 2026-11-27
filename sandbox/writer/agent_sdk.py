"""Claude Agent SDK glue for the writer (imported by agent_runner.main inside the image).

The coordinator tools are in-process SDK tools (``create_sdk_mcp_server``): calls go from the
CLI back to this process over the SDK control channel, and this process calls the coordinator
HTTP API. There is no network MCP server and no MCP config pointing at the coordinator.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

import agent_policy
import agent_runner
import coordinator_client
import coordinator_tools
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
) -> tuple[str, int]:
    tools = coordinator_tools.CoordinatorTools(client, tracker)

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

    log_path: Path = settings.out.parent / "cli.log"
    stop, turns = "error", 0
    with log_path.open("a", encoding="utf-8") as log:

        def on_stderr(line: str) -> None:
            log.write(line.rstrip() + "\n")

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
                    if tracker.over_budget:
                        stop = "budget"
                        await sdk.interrupt()
                        break
                elif isinstance(message, ResultMessage):
                    turns = message.num_turns
                    stop = agent_runner.stop_reason(message.subtype, message.is_error)
    return stop, turns
