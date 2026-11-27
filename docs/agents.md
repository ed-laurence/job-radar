# Agent dispatch policy (dev board)

Protected file: agents can't edit it. Full design: `docs/plan.md` → "Multi-agent system", "Sandboxing", "Code review".

## Scope
Dev-board tasks may change only `web/` and `api/` functionality. The write allowlist is in `docs/plan.md` → "Agent write scope". Anything else is `Out of scope`.

## Single or multi-agent
| Condition (risk derived from code; the LLM can only raise it) | Mode |
|---|---|
| One area (`web` **or** `api`), size S, low risk | Single `backend-engineer` or `frontend-engineer` |
| Both areas, **or** size ≥ M, **or** `api/openapi.yaml` changes | Multi-agent: `qa-expert` (tests) → contract subtask (if any) → `backend-engineer` ‖ `frontend-engineer` |
| Prompt-only change | `prompt-engineer` |
| Docs-only change | `documentation-engineer` |

## Dispatchable agents
`dev-lead`, `backend-engineer`, `frontend-engineer`, `qa-expert`, `prompt-engineer`, `documentation-engineer`.
Reviewers: `code-reviewer` (Gemini), `qa-expert` (review mode, Gemini). Security gate: `security-auditor` (Claude, in Actions).
**Never dispatched:** every agent marked HUMAN-ONLY in `.claude/agents/`.

## Limits
`MAX_ACTIVE_TASKS=2`, `MAX_PARALLEL_WORKERS=3`, `MAX_REVIEW_ROUNDS=2`, depth 1 (workers can't start agents). Message caps: brief ≤ 800 tokens, handoff ≤ 400, `request_help` ≤ 200, findings ≤ 10 × 60 words.

## Local sandbox
`scripts/dev.sh --with-agents` builds and starts it. Design: `docs/plan.md` → "Sandboxing".
- **Writer image** (`sandbox/writer/`): Python 3.12 + uv, Node 22 + pnpm, git, Postgres 16, Claude Code CLI and Agent SDK, all pinned. Backend and web dependencies are baked in from the lockfiles; runtime is offline (`UV_OFFLINE`, `npm_config_offline`, `COREPACK_ENABLE_NETWORK=0`). Run it only via `sandbox/run.sh`: `--read-only`, `--cap-drop ALL`, `no-new-privileges`, uid 10001, tmpfs `/workspace` and `/tmp`, network `jobradar_sandbox` (internal), no Docker socket.
- **I/O:** `/in/input.bundle` (must contain `HEAD`) → `/out/result.bundle` (branch `agent/<TASK_ID>`) + `/out/handoff.json` (handoff shape above). After the agent, the entrypoint re-runs the repo tests against the in-container Postgres, commits whatever changed on the branch and writes both files. Test counts and changed paths come from that re-run and `git diff`, never from the agent's claims.
- **Agent loop** (`sandbox/writer/agent_runner.py`, `AGENT_MODE=agent`, the default; `AGENT_MODE=stub` makes no edits and is what the isolation plumbing test uses):
  - Claude Agent SDK (pinned in `agent-requirements.txt`) driving the pinned Claude Code CLI. Model `claude-opus-5-5` (`AGENT_MODEL`), `AGENT_MAX_TURNS` (default 60), `AGENT_TOKEN_BUDGET` (default 3M, counted like llm-proxy; the run is interrupted when spent), `AGENT_TIMEOUT_S` wall clock (default 3600).
  - Container env: `ANTHROPIC_BASE_URL` (llm-proxy), `ANTHROPIC_AUTH_TOKEN` (jrt1), `COORDINATOR_URL` (HTTP API base, `http://coordinator:8100`; from `AGENT_COORDINATOR_URL`), `COORDINATOR_TOKEN` (jrc1), `TASK_ID`, `RUN_ID`, budgets. `sandbox/run.sh` passes them by name only.
  - System prompt: fixed writer rules + `AGENTS.md` + `docs/standards/*`, extracted from the **base commit** with `git archive` before the agent starts (`agent_prompt.py`). Nothing task-specific, so it caches.
  - Task: the runner calls `get_task` itself and puts the result, as JSON, in a nonce-delimited `<untrusted-task-data>` block of the **user turn**. Task text never reaches the system prompt; oversized tasks are refused, not cut.
  - Coordinator (ADR-0009, no MCP): `coordinator_client.py` is a thin client for the internal HTTP API (bearer `COORDINATOR_TOKEN`, JSON, RFC 9457 errors, 413 = oversized and refused): `GET /v1/task` (paged), `POST /v1/leases/{claim,renew,release}`, `/v1/steps`, `/v1/handoff`, `/v1/help`. `coordinator_tools.py` exposes `get_task`, `claim_paths`, `renew_lease`, `release_paths`, `post_step`, `post_handoff`, `request_help` to the agent as **in-process** Agent SDK tools (`agent_sdk.py`): no network MCP server and no MCP config. Only the runner process holds the token; it is removed from the env before the CLI starts. No user/project settings are loaded (`setting_sources=[]`, `strict_mcp_config`).
  - Tools (`agent_policy.py`, enforced in a PreToolUse hook and `can_use_tool`): Read/Glob/Grep inside `/workspace/repo`; Edit/Write only on paths the coordinator **granted** (`claim_paths` response; concrete paths or `dir/` prefixes) and never on protected paths (`.git/`, `AGENTS.md`, `docs/agents.md`, `docs/standards/`, `sandbox/`, `scripts/`, `infra/`, generated code, migrations, lockfiles); Bash only for `uv run pytest|ruff|pyright`, `pnpm test|lint|typecheck`, `scripts/check.sh --only <ids>` and `git status|diff|add|commit`, one command, no shell operators or expansion. No WebFetch, WebSearch or subagents. Advisory: the Publisher's checks are authoritative.
  - The agent posts `post_step` per meaningful step and ends with `post_handoff`; its summary, rubric IDs and open questions go into `handoff.json`. No `post_handoff`, a budget/turn/timeout stop, or a non-`done` agent status makes the handoff `failed`. Coordinator unreachable or `get_task` refused: the run fails closed with no edits.
- **Egress** (`scripts/lib/egress-proxy/`): sandboxes reach `llm-proxy:8080` and `coordinator:8100` directly on the internal network (`NO_PROXY`); squid also allows exactly those two name+port pairs, plus one model endpoint (`AGENT_MODEL_ENDPOINT=llm-proxy`, the `--with-agents` default → none; `vertex` → `*.googleapis.com:443`). Metadata, link-local and private ranges and every other host are denied.
- **llm-proxy** (`sandbox/llm-proxy/`, compose profile `agents`): holds the real `ANTHROPIC_API_KEY`; the sandbox gets only `ANTHROPIC_AUTH_TOKEN`, a per-task token `jrt1.<b64url{"t":task_id,"exp":unix,"b":budget}>.<b64url HMAC-SHA256(LLM_PROXY_SECRET, "jrt1.<payload>")>` (mint: `python -m llm_proxy.mint`). It enforces expiry (≤ 24 h), the per-task token budget (in memory) and the model allowlist, and logs metadata only. `LLM_PROXY_SECRET` lives in `~/.config/jobradar/llm-proxy.secret`.
- **`dev.sh --with-agents`:** builds the writer image and the compose `agents` images with full output in `.artifacts/agent-build.log` (tail shown on failure); creates `~/.config/jobradar/{llm-proxy.secret,coordinator.secret}` if missing (never printed); exports `LLM_PROXY_SECRET`, `COORDINATOR_TOKEN_KEY`, `AGENT_SANDBOX=docker`, `AGENT_SANDBOX_IMAGE`, `AGENT_REMOTE_PATH` and the other `AGENT_*` names (names logged, values never); starts `egress-proxy`, `llm-proxy` (needs `ANTHROPIC_API_KEY`) and `coordinator` (skipped with a warning while its compose service doesn't exist).
- **Tests:** `cd sandbox && uv run --no-project --with pytest==8.4.2 pytest -q` (unit, no network: tool policy, prompt assembly, untrusted-text delimiting, coordinator HTTP client and tools, handoff merge; pyright runs with the pinned `claude-agent-sdk` so `agent_sdk.py` is typed); `sandbox/tests/docker/isolation.sh [--build] [--snapshot]` (needs Docker; exit 77 = skipped): egress incl. coordinator reachable and example.com not, secrets, read-only FS, llm-proxy token refusals, stub plumbing, agent mode failing closed.
