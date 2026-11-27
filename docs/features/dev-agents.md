# Dev agents: the sandboxed run

A dev-board card's dev run executes in the local writer sandbox instead of the simulation, behind
the same `AgentRunner` port (`domain/ports/devboard.py`). Local only: prod refuses it. Design:
`docs/plan.md` "Sandboxing" and "Repository, commits and the GCP bootstrap"; container details:
`docs/agents.md` "Local sandbox".

## Switching it on

- `AGENT_SANDBOX=simulation` (the default, and the only value prod accepts) keeps
  `SimulatedAgentRunner`.
- `AGENT_SANDBOX=docker` (set by `scripts/dev.sh --with-agents`) wires `DockerAgentRunner` +
  `LocalGitHost` in `compose/devboard.py`. It needs `LLM_PROXY_SECRET` and
  `COORDINATOR_TOKEN_KEY` (each at least 32 bytes). Settings refuse to start without them.
- Plan runs (the read-only dev-lead) stay on the simulation for now. Only dev runs use Docker.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENT_SANDBOX_IMAGE` | `jobradar-sandbox-writer:dev` | writer image |
| `AGENT_SANDBOX_NETWORK` | `jobradar_sandbox` | internal compose network |
| `AGENT_EGRESS_PROXY` | `http://egress-proxy:3128` | the only way out |
| `AGENT_LLM_BASE_URL` | `http://llm-proxy:8080` | `ANTHROPIC_BASE_URL` in the sandbox |
| `AGENT_COORDINATOR_URL` | `http://coordinator:8100` | `COORDINATOR_URL` in the sandbox |
| `AGENT_REMOTE_PATH` | `~/.cache/jobradar/agent-remote` | scratch bare repo (must be outside the project) |
| `AGENT_RUN_TIMEOUT_S` | `3600` (60 to 82800) | wall-clock cap; the plan's `budget_minutes` can only lower it |

## One dev run (`adapters/agents/docker_runner.py`)

1. The run needs an approved plan; otherwise it fails before anything starts.
2. **Snapshot:** `LocalGitHost.snapshot` bundles the project's working tree (`HEAD` + `main`,
   one commit) into a private temp dir. See below for why this is the working tree.
3. **Tokens:**
   - an llm-proxy token `jrt1…` for `t = <task uuid>`, valid for the run's timeout, with a
     token budget of `budget_usd × 100 000` (the proxy's `LLM_PROXY_MAX_BUDGET` caps it again);
   - a coordinator token from `app.coordinator.tokens.mint` (task + run, TTL = the timeout).
4. **`docker run`** with exactly `sandbox/run.sh`'s hardening: `--read-only`, tmpfs `/tmp` and
   `/workspace`, `--cap-drop ALL`, `no-new-privileges`, uid 10001, pids, memory and CPU limits,
   `/in` read-only and `/out` writable. A unit test fails if `run.sh` and the runner drift apart.
   The runner calls `docker` directly (no bash needed on Windows) under the name
   `jr-sandbox-<task8>-<run8>`.
5. **Env:** `TASK_ID`, `RUN_ID`, the proxy variables, `ANTHROPIC_BASE_URL`, `COORDINATOR_URL`,
   `AGENT_TIMEOUT_S`, `AGENT_BUDGET_USD` and `AGENT_BUDGET_TOKENS` go in as `-e NAME=value`.
   `ANTHROPIC_AUTH_TOKEN` and `COORDINATOR_TOKEN` go in as `-e NAME` only. Their values sit in
   the docker CLI's own environment, which is built from an allowlist (PATH, Docker and profile
   variables), so it never includes `ANTHROPIC_API_KEY`, `LLM_PROXY_SECRET` or app secrets.
   Nothing is logged.
6. **Supervision:** the runner emits no steps (the agent posts them through the coordinator).
   It polls once a second.
   - On the kill switch (`is_cancelled`) or when the dispatcher cancels the task, it runs
     `docker kill`. The outcome is "Stopped." (or the task is cancelled).
   - When the wall clock runs out, it runs `docker kill`. The outcome is "Timed out after N s".
   - The docker client gets a grace period, then it's killed.
7. **Collect:**
   - `/out/handoff.json` must be at most 64 KiB, strictly typed (`adapters/agents/handoff.py`)
     and name this task. Anything else fails the run.
   - `/out/result.bundle` must exist.
   - `LocalGitHost.publish` then fetches `agent/<task uuid>` into the remote as
     `agent/<task8>-<run8>` and checks that it descends from the snapshot.
8. **Result:** a `SandboxResult` (an `AgentResult` with `branch`, `diff_stat` and the parsed
   `handoff`).
   - The outcome is built from counts only, e.g. `Branch agent/1a2b3c4d-5e6f7a8b: 2 files
     changed, +10 -2; handoff done; tests 5 passed, 0 failed.` The agent's free text is
     untrusted, so it stays in `handoff`.
   - The run succeeds only when the handoff's status is `done`. A `failed` handoff is still
     published, for review.
9. The temp dirs are always removed.

## LocalGitHost (`adapters/codehost/local_git.py`)

No git command names the project's own `.git`. Every call runs with no system or global config,
no hooks and `GIT_OPTIONAL_LOCKS=0`.

- **snapshot:** reads the working tree through a throwaway `--git-dir` and index in a temp dir
  (`add -A`, then commit, then bundle).
  - This works before the first commit, which is the repo's state today.
  - It includes uncommitted edits.
  - It leaves out anything `.gitignore`d, so keep secrets ignored.
  - The integration test hashes every file under the source `.git` before and after, and
    compares `status` and the refs.
- **publish / diff / diff_stat:** run with `--git-dir=<AGENT_REMOTE_PATH>` only. The bare remote
  is created on first publish.
  - Only `agent/<name>` branches can be written.
  - An existing branch is never overwritten.
- The `CodeHost` protocol lives next to it for now, until a domain port exists. `GitHubHost`
  replaces it after the initial commit.

## Processes (`adapters/agents/process.py`)

asyncio subprocesses only (stdlib).

- Under a loop without subprocess support (uvicorn `--reload` on Windows uses a
  SelectorEventLoop), the same code runs on a private loop in a worker thread.
- The stop signal is a `threading.Event`, so the kill switch still works in that mode.

## Tests

- `backend/tests/unit/agents_runner/`: a fake process runner and code host cover the argv
  hardening, secrets by name only, the env, both tokens, publishing and the branch name, failed
  and malformed handoffs, non-zero exits, the timeout, the kill switch, task cancellation, temp
  cleanup, plan delegation, settings (prod refusal, required keys, remote outside the project)
  and the compose switch.
- `backend/tests/integration/agents_runner/` (marker `integration`) uses real git against temp
  repos, plus the real process runner's stop path with the Python interpreter standing in for
  docker. Neither suite needs Docker; the container itself is covered by
  `sandbox/tests/docker/isolation.sh`.
