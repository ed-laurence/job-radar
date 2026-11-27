# Coordinator

The only app service sandboxed dev agents reach (besides `llm-proxy`). A small internal HTTP API
with strict JSON schemas, server-side size caps and task-scoped tokens (ADR-0009: not MCP). It
is not part of `api/openapi.yaml`; this page is its schema. Code: `backend/src/app/coordinator/`.

## Running it

- Locally: `cd backend && uv run python -m app.coordinator` (binds `127.0.0.1:8100`).
  App factory: `app.coordinator.server:create_app` (`build_app(runtime)` for tests).
- Compose: service `coordinator`, profile `agents`, built from `backend/coordinator.Dockerfile`.
  It is on the `sandbox` network as `coordinator` (base URL `http://coordinator:8100`) and on
  `app` for Postgres. Host port `127.0.0.1:8100` only, for debugging. Read-only root, no
  capabilities, uid 10003.

### Environment

| Variable | Meaning |
|---|---|
| `COORDINATOR_TOKEN_KEY` | HMAC key for tokens, at least 32 bytes. `dev.sh` keeps it in `~/.config/jobradar/coordinator.secret`. Never logged. |
| `DATABASE_URL` | The restricted app login (granted `jobradar_app`). The process refuses to start as a superuser or a `BYPASSRLS` role. Compose sets it from `COORDINATOR_DATABASE_URL` (host `postgres:5432`). |
| `COORDINATOR_HOST`, `COORDINATOR_PORT` | Bind address. Default `127.0.0.1:8100`; the image sets `0.0.0.0`. |
| `COORDINATOR_ALLOWED_HOSTS` | JSON list of accepted `Host` names (default `coordinator`, `127.0.0.1`, `localhost`; a `:port` suffix is ignored). Other hosts get 400. |
| `GUARDS`, `DENYLIST`/`DENYLIST_FILE`, GCP guard settings | As for the API: they configure the redaction filter. |

The database pool holds 2 connections. There is no LISTEN connection: step inserts NOTIFY
through the `run_steps` trigger, so the board's stream picks them up.

## Tokens

```
Authorization: Bearer jrc1.<b64url payload>.<b64url HMAC-SHA256(COORDINATOR_TOKEN_KEY, "jrc1.<b64url payload>")>
payload = {"t": "<task uuid>", "r": "<run uuid>", "exp": <unix seconds>, "aud": "coordinator"}
```

- Base64url is unpadded and canonical. The signature is compared in constant time before the
  payload is parsed. The payload allows exactly these four fields.
- Refused (401) when the token is missing, malformed, badly signed, expired (`exp <= now`), for
  another `aud`, or valid for more than 24 h from now.
- Refused (403) unless `r` is an active (queued or running) run of task `t`, top-level or a
  child writer run. This is checked on every request, so a killed run loses access at once.
- Mint: `uv run python -m app.coordinator.mint --task <id> --run <id> --ttl <s>` prints the
  token. Code: `app.coordinator.tokens.mint(key, task_id=..., run_id=..., ttl_s=..., now=...)`.
  The module is pure (stdlib and pydantic), so the runner adapter imports it.

## Scoping

No request carries a task or run id: they come only from the token, and every body schema
forbids extra fields (so `task_id` or `run_id` in a body is a 422). Requests run under row-level
security as the task's owner (`dev_tasks.created_by`), acting as an `agent`. The owner is looked
up under a maintainer's scope, which is possible because the board tables are shared.
Responses never name another task or run: a lease conflict reports only the leased path.

## Errors

RFC 9457 `application/problem+json`: `type` (`/problems/<code>`), `title`, `status`, `code`,
`correlation_id`, `instance`, optional `detail` and `errors` (`[{pointer, message}]`, never the
input). There are no SQL statements, hostnames or traces. 401 responses carry
`WWW-Authenticate: Bearer`.

| Status | `code` | When |
|---|---|---|
| 400 | (Starlette) | `Host` not allowed |
| 401 | `unauthenticated` | Token missing or refused |
| 403 | `forbidden` | Run isn't an active run of the token's task, or the task is gone |
| 409 | `illegal-transition` | `help` on a card that can't be blocked (for example `done`) |
| 413 | `too-large` | Body over 64 KiB, with or without `Content-Length` |
| 413 | `message-too-large` | A message over its cap (below). Refused, never cut |
| 422 | `validation-failed` | Wrong shape, type or value; unknown fields; bad paths |
| 422 | `result-too-large` | A response would be over 2k tokens (not expected; pages prevent it) |
| 503 | `unavailable` | Database or guard failure (logged by type only) |

## Caps

Tokens are estimated as `max(chars / 4, words * 4 / 3)` over the JSON
(`app.domain.devboard.messages.estimate_tokens`).

- Request body ≤ 64 KiB. Step text ≤ 2000 characters. Claim ≤ 50 paths, `wait_s` ≤ 60.
- Handoff ≤ 400 tokens, `summary` ≤ 80 words, `open_questions` ≤ 3, no code fences, diff
  hunks or tracebacks.
- Help ≤ 200 tokens (counted with the run id, as the board's `RequestHelp` message).
- Every response ≤ 2000 tokens; `GET /v1/task` pages to stay under it.
- Every response string passes the redaction filter (secret patterns, denylist, PII, output
  guard), except server-generated ids, enums and timestamps.

## Endpoints

All `/v1` endpoints need the token. Bodies are JSON objects; `{}` or an empty body is fine
where nothing is required.

### `GET /healthz`

No token. `200 {"status": "ok"}`.

### `GET /v1/task?cursor=<n>`

`cursor` (optional, default 0, ≤ 10000) is the previous page's `next_cursor`. No other query
parameters and no body.

```json
{
  "task": {"id": "uuid", "title": "str", "column": "agent_dev", "type": "feature|null",
           "size": "S|null", "risk": "low|null", "areas": ["web"],
           "labels": ["modify-tests:web/src/features/devboard/BoardMetrics.test.tsx"]},
  "run": {"id": "uuid", "kind": "dev", "agent": "frontend-engineer", "status": "running", "round": 0},
  "budget": {"usd": 1.0, "minutes": 30},
  "plan": {"mode": "single", "version": 1, "approved": true},
  "brief": "this page's part of the task body",
  "acceptance_criteria": [{"test_id": "str", "expectation": "str"}],
  "acceptance_tests": ["str"],
  "tests_to_change": ["str"],
  "subtasks": [{"id": "s1", "title": "str", "agent": "str", "lease_paths": ["web/src/features/"],
                "depends_on": [], "is_test_only": false}],
  "lease_paths": ["web/src/features/"],
  "leases_held": [{"path": "str", "kind": "exclusive|hotspot", "expires_at": "ISO 8601"}],
  "brief_complete": true,
  "next_cursor": null,
  "total_items": 6
}
```

- `plan` is null before planning, and `budget.usd` is the run's budget, else the plan's.
- `labels` lets the writer see which existing tests a `modify-tests:` label opens for edits;
  `tests_to_change` is the approved plan's list of them.
- Plan runs (`kind: plan`, the read-only dev-lead) may call `GET /v1/task`, `/v1/steps`,
  `/v1/handoff` and `/v1/help`; lease claims return 403.
- `lease_paths` holds the lease of the subtask whose `agent` matches the run's agent, or every
  subtask's paths when none matches (the lead).
- Pages: the header repeats on every page; the brief (in chunks of about 1500 characters), the
  criteria, tests, tests to change, subtasks, lease paths and held leases fill pages in that order. Join
  `brief` across pages. `next_cursor` is null on the last page.

### `POST /v1/leases/claim`

Request `{"paths": ["web/src/features/contacts/"], "wait_s": 0}`: 1 to 50 concrete repo paths
or directory prefixes ending in `/` (no globs), inside the agent write scope. A path in a
single-writer hotspot leases the whole hotspot. The claim is all or nothing with a 10-minute
TTL (see `domain/devboard/leases.py`).

If another run holds an overlapping lease, the call waits server-side up to `wait_s` seconds
(the wait is queued once in `lease_waits`, and the server re-checks every second without
re-queueing), then answers:

```json
{"granted": [{"path": "str", "kind": "exclusive|hotspot", "expires_at": "ISO 8601"}],
 "waiting": [], "waited_s": 2}
```

On a timeout, `granted` is empty and `waiting` lists the conflicting leased paths. If the run
stops being active while it waits, the call ends with 403.

### `POST /v1/leases/renew`

Request `{}`. Heartbeats every live lease of this run for another 10 minutes:
`{"renewed": 2, "leases": [{"path", "kind", "expires_at"}]}`.

### `POST /v1/leases/release`

Request `{}` or `{"paths": [...]}`. Releases this run's leases: `{"released": 2}`. With `paths`,
they must cover every lease the run holds; a partial release is a 422 for now (the lease store
releases by run or task only).

### `POST /v1/steps`

Request `{"kind": "thought|tool_call|tool_result|message", "text": "1..2000 chars"}`. The text
is redacted, stored as the run's next step (attributed to the run's agent) and NOTIFY'd, so the
board streams it. Response `{"seq": 3}`.

### `POST /v1/handoff`

The body is the AGENTS.md handoff object:

```json
{"status": "done|partial|blocked|failed", "summary": "<= 80 words",
 "bundle_ref": null, "changed_paths": ["..."],
 "tests": {"passed": 3, "failed": 0, "first_failing": null},
 "rubric_checked": ["ARCH-01"], "open_questions": [], "artifacts": [],
 "skipped_checks": ["none"]}
```

Validated against `app.domain.devboard.messages.Handoff` plus `skipped_checks` (≤ 10). Stored as
a `handoff` step. Response `{"accepted": true, "seq": 4, "status": "done"}`.

### `POST /v1/help`

Request `{"text": "<= 200 tokens"}`. Records a `message` step and moves the card to `blocked`
(the redacted text becomes `blocked_reason`, the event names the run). Asking again on a blocked
card succeeds without moving it. Response `{"column": "blocked", "seq": 5}`.

## Tests

- `backend/tests/unit/coordinator/`: token mint and verify (with Hypothesis tampering
  properties), the mint CLI, and every operation's scoping, caps and lease waits over in-memory
  stores and a fake clock.
- `backend/tests/contract/coordinator/test_coordinator_http_contract.py`: the HTTP contract
  above, including every 401/403 refusal and every cap.
- `backend/tests/integration/coordinator/`: real Postgres as the restricted login (owner scope,
  NOTIFY, cross-task lease waits, Blocked, the RLS start-up check, a pool of 2).
