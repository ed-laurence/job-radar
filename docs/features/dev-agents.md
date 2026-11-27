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
- Plan runs (the read-only dev-lead) stay on the simulation for now. Only dev runs use Docker,
  and they are labelled with the sandbox's role, `writer` (the run's `agent`), not `dev-lead`.
  `compose/devboard.py` applies the label (`LabelledRunStore`) because the workflow's default
  label is per run kind, not per runner.

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
   - an llm-proxy token `jrt2…` for `t = <task uuid>` with the plan's USD budget
     (`usd_micro = round(budget_usd × 1 000 000)`, at least 1), valid for the run's timeout
     plus 300 s so the spend can still be read after a timeout kill (format below);
   - a coordinator token from `app.coordinator.tokens.mint` (task + run, TTL = the timeout).
4. **`docker run`** with exactly `sandbox/run.sh`'s hardening: `--read-only`, tmpfs `/tmp` and
   `/workspace`, `--cap-drop ALL`, `no-new-privileges`, uid 10001, pids, memory and CPU limits,
   `/in` read-only and `/out` writable. `test_run_sh_parity.py` parses `run.sh` and fails if
   the runner's flags, its fixed env values (e.g. `NO_PROXY`) or its env names drift from it.
   The runner calls `docker` directly (no bash needed on Windows) under the name
   `jr-sandbox-<task8>-<run8>`.
5. **Env:** exactly `run.sh`'s. `TASK_ID`, the proxy variables, `NO_PROXY`/`no_proxy` =
   `localhost,127.0.0.1,llm-proxy,coordinator` (both internal services are reached directly,
   never through squid), `ANTHROPIC_BASE_URL`, `COORDINATOR_URL`, `RUN_ID` and
   `AGENT_TIMEOUT_S` go in as `-e NAME=value`. The budget travels only in the `jrt2` token.
   `ANTHROPIC_AUTH_TOKEN` and `COORDINATOR_TOKEN` go in as `-e NAME` only. Their values sit in
   the docker CLI's own environment, which is built from an allowlist (PATH, Docker and profile
   variables), so it never includes `ANTHROPIC_API_KEY`, `LLM_PROXY_SECRET` or app secrets.
   Nothing is logged. The docker client's own stdout and stderr go to a `runner.log` in the
   private temp dir (outside both mounts).
6. **Supervision:** the runner emits no steps (the agent posts them through the coordinator).
   It polls once a second.
   - On the kill switch (`is_cancelled`) or when the dispatcher cancels the task, it runs
     `docker kill`. The outcome is "Stopped." (or the task is cancelled).
   - When the wall clock runs out, it runs `docker kill`. The outcome is "Timed out after N s".
   - The docker client gets a grace period, then it's killed.
7. **Collect** (also after a non-zero exit, because the writer exits 1 with a failed handoff):
   - `/out/handoff.json` must be a regular file (not a symlink) of at most 64 KiB, strictly
     typed (`adapters/agents/handoff.py`) and name this task.
   - `/out/result.bundle` must exist.
   - `LocalGitHost.publish` then fetches `agent/<task uuid>` into the remote as
     `agent/<task8>-<run8>` and checks that it descends from the snapshot.
8. **Spend:** `GET http://llm-proxy:8080/v1/usage` with the run's `jrt2` token (format below).
   llm-proxy publishes no host port, so `adapters/agents/usage.py` asks from the sandbox network:
   a one-shot `docker run` of the writer image with the same hardening, `--entrypoint python3`
   (stdlib `urllib`, no proxy) and the token as `-e LLM_PROXY_TOKEN` (by name). The answer sets
   the run's `cost_usd`. If it fails in any way (docker, network, status, body), the spend is
   **unknown**, not zero: `SandboxResult.spend` is `None` and the outcome ends with "Model spend
   unknown (llm-proxy usage unavailable)."
9. **Result:** a `SandboxResult` (an `AgentResult` with `branch`, `diff_stat`, the parsed
   `handoff` and `spend`). The outcome is the card's blocked reason when the run fails.
   - `done`: built from counts only, e.g. `Branch agent/1a2b3c4d-5e6f7a8b: 2 files changed,
     +10 -2; handoff done; tests 5 passed, 0 failed.` The run succeeds.
   - `failed` / `error`: the handoff's summary is the reason ("Stopped: model budget used up…",
     "coordinator unreachable…"). It is untrusted text, so it is made one line (ANSI and control
     characters removed), capped at 300 characters, scrubbed of the run's tokens and redacted.
     After a clean exit the branch is still published for review and its counts follow.
   - no handoff, or an invalid one: "The agent produced no handoff (see
     .artifacts/agent-runs/<run_id>/)." (prefixed with "The sandbox failed (exit N);" when the
     container exited non-zero).
   - `done` but a non-zero exit: "The sandbox failed (exit N)."
10. **Artifacts:** `.artifacts/agent-runs/<run_id>/` (gitignored with `.artifacts/`) keeps
    `handoff.json`, `agent.log` and `steps.jsonl` from `/out` (when present, regular files only,
    last 256 KiB each) and `runner.log` (the docker client's output, last 64 KiB). Every file has
    the run's llm-proxy and coordinator tokens and `LLM_PROXY_SECRET` replaced by `[redacted]`,
    then goes through the guard redactor. Only the newest 20 run folders are kept; other entries
    are left alone. The result bundle is not kept (it is published).
11. The temp dirs are always removed.

## llm-proxy tokens (`adapters/agents/tokens.py`)

The proxy side (`sandbox/llm-proxy`) must verify exactly this. `b64url` is URL-safe base64 with
the `=` padding stripped; the payload is compact JSON (`separators=(",", ":")`) with sorted keys.

```
jrt2.<b64url(payload)>.<b64url(HMAC-SHA256(LLM_PROXY_SECRET, "jrt2." + b64url(payload)))>
payload = {"exp": <unix seconds, int>, "t": "<task id>", "usd_micro": <int > 0>}
```

- `t` matches `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` (the task UUID). `usd_micro` is the task's
  budget in micro-dollars (1 USD = 1 000 000), priced and enforced by the proxy.
- Test vector (`tests/unit/agents_runner/test_llm_proxy_jrt2.py`): secret
  `jrt2-vector-secret-not-real-0123456789`, `t = task-1`, `exp = 1800000600`,
  `usd_micro = 2500000` gives
  `jrt2.eyJleHAiOjE4MDAwMDA2MDAsInQiOiJ0YXNrLTEiLCJ1c2RfbWljcm8iOjI1MDAwMDB9.2ZmGKhbuHMcWHpT9sOGz3vqP-I9MxSTuPygJQ9iuz1g`.
- `jrt1` (`{"b": <tokens>, "exp", "t"}`, same framing) is still minted by
  `mint_llm_proxy_token`; the dev runner no longer uses it.
- `GET /v1/usage` with `Authorization: Bearer <jrt2 token>` answers 200 with
  `{"usd_micro_used": int, "usd_micro_budget": int, "requests": int}` (all `>= 0`, body at most
  4 KiB) for the token's task. It must accept the token until its `exp` (the runner reads it
  right after the container exits).

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

- `backend/tests/unit/agents_runner/`: a fake process runner, code host and usage probe cover
  the argv hardening, secrets by name only, the env, both tokens (plus the `jrt2` vector),
  publishing and the branch name, failed and malformed handoffs, non-zero exits, the timeout,
  the kill switch, task cancellation, temp cleanup, plan delegation, settings (prod refusal,
  required keys, remote outside the project) and the compose switch.
  - `test_run_sh_parity.py`: parses `sandbox/run.sh` and compares flags, fixed env values and
    env names with the runner's argv (and proves it catches the `NO_PROXY` drift that shipped).
  - `test_docker_runner_reporting.py`: spend known and unknown, the failure reason, kept and
    pruned artifacts (never holding a token), the usage probe and the `writer` label.
- `backend/tests/integration/agents_runner/` (marker `integration`) uses real git against temp
  repos, plus the real process runner's stop path and `output` capture with the Python
  interpreter standing in for docker. Neither suite needs Docker; the container itself is covered by
  `sandbox/tests/docker/isolation.sh`.
