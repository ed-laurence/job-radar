# Dev board

A shared Kanban board for developing this app. Cards run AI agents; a server-side state machine decides every move. Spec: `api/openapi.yaml` tags "Dev board" and "Runs".

## Columns and moves
`backlog → triaged → plan_review → ready → agent_dev → agent_review → human_review → done`, plus `out_of_scope`, `changes_requested` and `blocked`.

User moves (`POST /tasks/{id}/move`). Anything else is **422 `illegal-transition`** with `allowed_columns`; a stale `version` is **409** with `current_version` (checked first).

| from | to | who |
|---|---|---|
| any | same column (reorder) | anyone |
| `out_of_scope` | `backlog` (triage re-runs) | anyone |
| `triaged` | `plan_review` (starts the plan) | maintainer |
| `plan_review` | `ready` (needs approved tests + plan, else 422 `plan-not-approved`) | maintainer |
| `ready` | `agent_dev` (starts the dev run) | maintainer |
| `triaged`, `plan_review`, `ready` | `backlog` | anyone |
| any except `done`, `agent_dev`, `agent_review` | `blocked` (`reason` required) | anyone |
| `blocked` | the column it came from, or `backlog` | anyone |

Each card in the API carries `allowed_columns`, the columns the caller may move it to now (from the state machine and their role); while a card is dragged the board outlines those columns green and the rest red and dashed (screen readers hear "Can't drop here"). A user asking for a maintainer move gets **403**. A card blocked from `agent_dev`/`agent_review` (the kill switch) can only go back to `backlog`. `out_of_scope` can never reach `ready`, and nothing reaches `ready` without an approved plan, whichever path it takes.

System moves: triage (`backlog → triaged | out_of_scope`), runs (`agent_dev → agent_review`, `agent_review → human_review | changes_requested`, `changes_requested → agent_dev`), the merge webhook (`human_review → done`) and the kill switch (anything open → `blocked`).

**Ranks** are base-62 fractional keys compared in byte order. A client rank that collides with another card's is moved just after it; when no key fits, the server rebalances the column (those cards' versions go up). No two live cards in a column share a rank.

## Creating and editing
Title and body are untrusted text. PII is redacted (`[redacted]`) before anything is saved, and `pii_redacted` tells the author; a `pii_redacted` event is added. Editing the title or body of a card in `backlog`, `triaged` or `out_of_scope` re-runs triage. `modify-tests:` and `allow:` labels are maintainer-only. Delete is soft and refused while a run is active.

## Triage (`app/agents/triage`)
Triage does three things and writes **no acceptance criteria** (the planner writes those):
1. **Verify and categorise.** Is the issue real and reproducible, and is it a duplicate of an open card? Type: feature, bug, security, performance, chore, refactor, test or docs. The verification records a status (`confirmed`, `not_reproduced`, `unclear`, `duplicate`), the evidence, the existing repo paths it concerns (`code_refs`) and, for a duplicate, the card it duplicates.
2. **Assess impact and urgency.** Priority `P0` (drop everything) to `P3` (whenever), and severity (`critical`, `major`, `minor`, `trivial`) from user impact, blast radius and any deadline, with a short impact note.
3. **Route.** `now`, `next` or `later`, with a reason.

Steps, each stored and streamed: guard check (Model Armor; blocked text fails the run and the card stays in `backlog`), PII masking, then the agent. With `dev.sh --with-agents` triage runs read-only in the writer sandbox (`AGENT_MODE=triage`, Sonnet, $0.50 and 5 minutes by default): it reads and searches the repo, can run existing tests to reproduce a bug, sees the other open cards, and submits through `submit_triage`, which refuses paths that aren't in the repo. Without the sandbox, one structured-output call (feature `triage`) gets the task, the list of writable repo files and the open cards; if it names a path that isn't in the repo it is asked once more, then the run fails.

Code then decides:
- **Likely paths** (where triage expects the change to land) set risk and scope, are stored with the triage and shown on the card, and plan runs get them from `get_task` as a starting point.
- **Paths are real**: every `code_refs` path and likely path must be an existing file or directory, or a new file in an existing directory (`domain/devboard/repo_paths.py`), checked against the working tree. Otherwise the triage run fails and names the paths.
- **Route**: a duplicate or unreproduced issue goes `later`; `unclear` or needs-human-design goes no further than `next`. `now` moves the card to `plan_review` and starts planning; `next` ranks it at the top of `triaged`, `later` at the bottom. The route doesn't depend on who made the card: the maintainer's and the tester's cards are routed the same way. A duplicate must name the open card it duplicates, or triage fails. You can drag it anywhere.
- **Risk** = the path-risk map over the likely paths (shared web code in `web/src/lib/` is high; `api/openapi.yaml`, generated code, `backend/src/app/{api,domain}/` and `web/src/app/` medium, since contract edits are hotspot-locked, planned as their own subtask and covered by contract tests; features, components, tests and feature docs low), maxed with the model's estimate. The model can only raise it.
- **Scope**: any likely path outside the agent write allowlist (infra, CI, harness, auth/config modules, adapters, migrations, test infrastructure, lockfiles…) makes the card `out_of_scope` with the reason. The model can mark a card out of scope but can't bring one back.

Offline (`LLM=replay`) the board replays `backend/tests/fixtures/llm/devboard/`; a card without a recording fails triage with "No offline recording".

## Plan review
Entering `plan_review` (or `POST /tasks/{id}/plan`, maintainer) runs the read-only dev-lead and the test-only step. The plan holds mode (single for one area + size S + low risk, else a team), subtasks with lease paths (the `qa-expert` test-only subtask first; a contract subtask on `api/openapi.yaml` for API teams), the acceptance criteria (`<test file>::<test name>` + expectation, written by the planner from the card text, including any `## Acceptance criteria` bullets, and triage's verification; each test file exists or is new in an existing test directory), the frozen acceptance tests, a budget, and risk from the path-risk map over every lease path. With `dev.sh --with-agents` the dev-lead runs read-only in the writer sandbox and checks every path against the real repo; otherwise a template planner stands in. Code then adds the regenerated dirs to a contract subtask and `backend/tests/contract/` to the test-only subtask, and refuses a plan with unusable test paths. The plan also lists `tests_to_change`: existing test files the change must edit (existing tests are otherwise read-only for agents). `POST /tasks/{id}/plan/approve` approves tests and plan together (409 if either version is stale) and moves the card to `ready`. When the plan lists tests not covered by a `modify-tests:` label, approval needs `allow_test_changes: true` (the "Allow the agent to edit these tests" box; 422 otherwise) and adds a `modify-tests:<path>` label per test.

## Runs and steps
`GET /tasks/{id}/runs` (newest first), `GET /runs/{id}`, and `GET /runs/{id}/steps` (SSE): saved steps after `Last-Event-ID`, then live ones as `step` events, then `done` with the run status (or `error`). Step text is redacted before it is stored and again on the way out. The stream listens on the process's shared `run_steps` LISTEN hub; offline, an in-memory hub.

Until M2 the dev run is **simulated** (`SimulatedAgentRunner`): it claims the plan's lease paths, emits realistic steps with short pauses, renews its leases, releases them, and moves the card to `agent_review`. A lease conflict fails the run and blocks the card.

**Kill switch** (`POST /tasks/{id}/kill`, maintainer): stops every active run of the card, releases its leases and moves it to `blocked`. Calling it again on a blocked card with no run returns the card unchanged.

## Leases
Concrete paths or directory prefixes (trailing `/`), never globs. Two paths overlap when one's segments are a prefix of the other's. Claims are all-or-nothing and atomic (Postgres: one transaction-scoped advisory lock); a refused claim records a `lease_waits` row. A path inside a hotspot (`api/openapi.yaml`, `backend/src/app/generated/`, `web/src/lib/`) is leased as the whole hotspot: one writer at a time. Leases have a TTL (30 s – 1 h, default 10 min) and a heartbeat; expired leases stop counting immediately and the watchdog marks them released.

## Coordinator message caps (`app/domain/devboard/messages.py`)
Brief ≤ 800 tokens, handoff ≤ 400 (summary ≤ 80 words, ≤ 3 open questions, rubric IDs only, no code/diffs/tracebacks), request_help ≤ 200, findings ≤ 10 per reviewer and ≤ 60 words each. Tokens are estimated as max(chars / 4, words × 4/3). Oversized messages are refused with `validation-failed`, never truncated.
