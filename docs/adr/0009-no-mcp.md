# ADR-0009: No MCP

- Status: accepted
- Date: 2026-11-27

## Context
The plan used MCP in three places:
1. a user-facing server at `/mcp`, so external clients (Claude Code, Claude Desktop) could search roles, update applications and create or move dev-board tasks;
2. the internal `coordinator` that sandbox agents use to read their task, claim path leases, post steps and hand off;
3. the dev harness (`.mcp.json`: Playwright and GitHub servers).

None of it was built. The user-facing server was a BFF proxy route and an empty backend package; the coordinator and the harness config didn't exist.

- **User-facing server:** the owner doesn't need to drive the app from an external client. Every feature is reachable from the web UI, and in-app agents call domain services through tool-runner tools (ADR-0004). It would also need an OAuth authorization server inside `api`, because Identity Platform alone can't meet the MCP authorization spec: new sign-in code and a second public entry point, for a feature the product doesn't depend on.
- **Coordinator:** what matters is enforced on the server either way (strict schemas, refused oversized messages, task-scoped tokens, a blocking `claim_paths`). MCP's only gain was that Claude Agent SDK and Gemini CLI consume it without glue, and that doesn't outweigh adding a protocol layer to a seven-operation internal service.
- **Harness:** not needed.

## Decision
- No user-facing MCP server. The `web` proxy route for `/mcp`, its MCP headers in `lib/api/bff` and the placeholder `backend/src/app/mcp/` package are removed.
- The coordinator is a small internal HTTP API (internal-only ingress, task-scoped tokens, strict schemas and size caps). Each agent runtime gets a thin tool wrapper over it.
- No MCP servers in the dev harness.
- The `mcp-developer` agent is renamed `coordinator-developer`.

## Consequences
- One less public surface; no OAuth server to build or red-team.
- One thin wrapper per agent runtime (Agent SDK, Gemini CLI) to write and keep in step with the coordinator API.
- `mcp` stays in `sandbox/writer/agent-requirements.txt` as a transitive dependency of `claude-agent-sdk`; it isn't used.
- If an in-app dev-board chatbot is added later, it uses tool-runner tools over the dev-board domain service.
- To restore a user-facing server later: write the tool functions once over domain services, wrap them with the Python MCP SDK at `/mcp` on `api`, add the authorization server, and add back the `web` proxy route.
