# Plan: Job Radar

## Context
Goal: an AI-assisted job-search app, plus a dev board whose AI agents develop the app itself, built with rigorous, harnessed AI-assisted development. The app uses AI actively: it generates, classifies, extracts, decides and acts.

It must run robustly on GCP (Model Armor, Sensitive Data Protection, web analytics, performance and uptime monitoring), and a single shell script must run it locally.

Starting point: `C:\Users\edcla\Documents\projects\general\job-seeker` (job-radar). It's a single-user local FastAPI + SQLite + vanilla-JS app with good AI domain logic and ~237 offline pytest tests, but no CI, typing, migrations, auth or cloud setup.

**Decisions made:**
- New repo; port job-radar's domain logic module by module.
- Python backend, Next.js frontend.
- GCP: Claude on Vertex AI, Cloud SQL `db-f1-micro` on a private IP.
- Environments: local and prod only.
- GitHub + Actions, but only **after** the initial commit.
- Spec-first OpenAPI.
- Multi-user with sign-in.
- Agent loops are self-hosted on Cloud Run.
- The dev-board agents work on this repo.
- All demo data is dummy data derived from job-seeker's scraped data.
- **Cross-model code review:** Claude writes the code, and Gemini on Vertex reviews it, against one shared standards pack.
- **No registration.** Exactly two provisioned accounts, the owner and a tester (see Access control). The emails are config, not code.
- **The dev board only accepts frontend and API functionality work.** Anything needing an infra change is out of scope, and agents can't touch the harness.
- **No exposure** of API keys, GCP project details, owner details or PII through the app, API, agent steps, PRs, the frontend bundle or the repo (see Data exposure).

### Changes in this revision (review findings)
1. **Workflow:** no git commits until you ask. All development is local. The initial commit is the trigger that provisions GCP through OpenTofu; it needs a one-time bootstrap first.
2. **Sandbox hooks can be bypassed.** An agent with Bash can get around its own hooks, so the authoritative checks move **outside** the sandbox to a trusted Publisher.
3. **Sandboxes hold no credentials and have no internet.** There's no GitHub token inside. Agents output a git bundle, and the only network access is to Google APIs via Private Google Access. This is also cheaper than an egress proxy.
4. **Against reward hacking:**
   - Acceptance tests are written first.
   - The Publisher verifies the tests fail on the base and pass on the head.
   - Adding skip/ignore pragmas is blocked.
   - A cap on PR size.
5. **Approval gates:** a human approves the work plan for multi-agent and high-risk tasks before any writer starts.
6. **Coder and reviewer stay aligned** through the standards pack: `AGENTS.md`, a rubric with IDs, a shared findings schema, calibration, and escalation rules.
7. **Build order** gets the job-search features and the dev board demoable by milestone M1. The multi-agent and cross-model work are layered on top, behind flags.

## Product: two areas in one app
**A. Job search (ported from job-radar):**
- Roles and applications, contacts, campaign targets.
- AI scoring and extraction, application kits, role chat.
- A company-research agent.

**B. Dev board (new):** a Kanban board for developing *this app*.
- Columns: `Backlog → Triaged → Plan review → Ready → Agent dev → Agent review → Human review → Done`, plus `Changes requested` and `Blocked`.
- Some drops start automation. A server-side state machine enforces which moves are allowed.

## Dev-board flow
1. **Triage:** an in-app agent using the Anthropic SDK tool runner with `AnthropicVertex` and structured outputs.
   - Output: type, size, risk, areas (`web` and/or `api` only), **executable acceptance criteria** (test names and expectations, not prose), and a "needs human design" flag.
   - **Scope check:** if the task would need infra, CI, sandbox, harness, auth/guards, new env vars, secrets or GCP services, triage marks it **`Out of scope`** with the reason. The state machine won't let it move to `Ready`; a human must rewrite it or handle it outside the board.
   - Task text is SDP-inspected on create and edit. PII is redacted before it's saved, and the author is told.
   - The card moves to `Triaged`.
2. **Plan:** the dev-lead runs in read-only mode and writes a work plan: single or multi-agent, subtasks, leases, dependencies, budget.
   - **Acceptance tests are frozen first.** A test-only step writes the failing acceptance tests, and a **human approves them** in `Plan review` for every task. In single-agent tasks the same agent would otherwise write both the tests and the code, so red→green would prove nothing.
   - **Risk comes from code, not from the LLM.** Lease paths are matched against a path-risk map (e.g. the `api/openapi.yaml` contract, shared `web/src/lib`). The LLM's estimate can only *raise* the risk.
   - **Every task goes through `Plan review`**, because the acceptance tests always need human approval. For low-risk, size-S tasks the review is a one-click "approve tests + plan".
   - **Behaviour-changing tasks:** a human-applied `modify-tests:<paths>` label lets the agent change specific existing tests. Without the label, existing tests stay read-only.
   - Everything else waits in `Plan review` until a human approves the plan by dragging the card to `Ready`.
3. **Agent dev:** maintainers only. One agent, or a multi-agent team (see below). Output is a git bundle plus a handoff, never a push.
4. **Publish:** the trusted Publisher job, which is not an agent (see Sandboxing), verifies the bundle, then pushes the `agent/*` branch using a **deploy key** created by OpenTofu and stored in Secret Manager.
   - Repo rulesets block that key from pushing to `main`, tags or force-pushing.
   - The push triggers CI, and the `agent-pr.yml` workflow (`GITHUB_TOKEN`, `pull-requests: write`) opens the PR, with a link to the card in the PR body.
   - Review findings live in the app and are linked from the PR.
   - Before GitHub exists, `LocalGitHost` stands in for this step.
5. **Agent review:** cross-model panel (see Code review).
   - Zero `BLOCKER`s: auto-approved, and the card moves to `Human review`.
   - Otherwise: `Changes requested`, and dev re-runs fresh with only the blockers, up to 2 rounds.
   - A deadlock or the cap being hit escalates to `Human review` flagged `needs decision`.
6. **Human review:** the card shows the PR, CI, the review verdict and any disputes. A human merges on GitHub, and a webhook moves the card to `Done`.

Agents can never merge or deploy.

**Agent write scope is an allowlist, not a denylist** (`docs/agents.md`, enforced by the Publisher):
- **Allowed:**
  - `web/src/**`, `web/tests/**`
  - `backend/src/app/{api,domain}/**`
  - `backend/tests/{unit,property,contract,integration}/**` (new tests only; existing tests are read-only)
  - `api/openapi.yaml`, behind the hotspot lock
  - `*/generated/**`, regenerated by the lead only
  - `docs/features/**` only. Not `docs/agents.md`, `docs/security.md`, `docs/standards/**` or ADRs, so an agent can't plant instructions that later agents read on `main`.
- **Test-infrastructure files are never writable**, even inside `tests/`: `conftest.py`, pytest plugins and config, `sitecustomize.py`, `*.pth`, Vitest/Playwright config and setup files, `package.json`, `pyproject.toml`.
- **Leases are concrete paths or directory prefixes** (e.g. `web/src/features/board/`), not arbitrary globs, so overlap checks are exact.
- **Never writable by agents**, even with a label:
  - `infra/**`, `.github/**`, `sandbox/**`, `publisher/**`, `scripts/**`, Dockerfiles, `docker-compose.yml`
  - `.claude/**`, `AGENTS.md`, `docs/standards/**`, `review/**`
  - `backend/src/app/{adapters,guards,agents,jobs}/**` and the auth and config modules
  - `backend/migrations/**`
  - lockfiles. Dependency changes are out of scope, so humans add packages.
- Tasks that need any of these are `Out of scope`. Humans do that work in Claude Code with the normal harness.

## Sandboxing
> **Revised after independent review.** Where this section conflicts with the older text below, this block wins.
> - **The Publisher is split in two:**
>   - **`verify`** runs *all* repo code (red→green, formatting and lint checks, regenerating generated code) inside a sandbox with **no credentials**. It outputs a verdict signed with a KMS key, plus the bundle SHA.
>   - **`push`** runs **no repo code**. It checks the signature and SHA, then pushes with the deploy key. Only `push` can read the deploy key.
> - **The network is closed:**
>   - A **VPC Service Controls perimeter** around the project, with `restricted.googleapis.com` DNS. Sandbox and red-team jobs can reach only this project's Vertex, GCS and Model Armor; no Google API in another project.
>   - **No package installs at runtime.** Dependencies are baked into the image (pinned by lockfile, image pinned by digest), so there are no PyPI/npm mirrors in the sandbox.
> - **Tests in the sandbox:** Postgres is **baked into the sandbox image** (started inside the container) instead of Testcontainers, because Cloud Run Jobs have no Docker daemon. Testcontainers is used only on dev machines and in CI.
> - **Runtime:** Cloud Run Jobs run on the **gen2 execution environment, a microVM**. "gVisor" is dropped from the wording; the isolation boundary is the microVM plus a non-root user and a read-only filesystem.
> - **WIF scope:** two GitHub OIDC providers.
>   - The **deploy** provider gives credentials only to `refs/heads/main` + `environment:prod`.
>   - The **review** provider and service account can **only call Vertex** (`roles/aiplatform.user`). It's bound via `job_workflow_ref` to `security-review.yml@refs/heads/main`.
>   - Workflows running from `agent/*` branch code never get GCP credentials.
>
> **Second review fixes:**
> - **Verify is split three ways, so verdicts can't be forged:**
>   1. **`test-runner`** runs the repo code and has **no KMS, no Secret Manager and no GitHub access**. It writes raw results to GCS.
>   2. **`signer`** runs **no repo code**. It checks:
>      - the bundle SHA;
>      - that the frozen acceptance-test files' SHA-256 matches the commit approved in Plan review;
>      - that **no test-infrastructure files** were added or changed: `conftest.py`, pytest plugins/`pytest.ini`/`[tool.pytest]` edits, `pyproject.toml`, `sitecustomize.py`, `*.pth`, Vitest/Playwright config or setup files, `package.json` scripts;
>      - the allowlist and the denylist.
>
>      Then it signs with KMS.
>   3. **`push`** checks the signature and pushes. It runs **outside the VPC** on default Cloud Run egress to reach github.com, and is the only job that can read the deploy key.
>   - `test-runner` also **overlays the trusted `conftest.py` and test config from the base commit** before running.
>   - **CI re-runs red→green independently** on GitHub runners as a required check (`ci.yml`).
>   - The PR security gate includes Semgrep rules for **test-environment detection** in source files (e.g. `"pytest" in sys.modules`, `process.env.VITEST`).
> - **"Low-value, scoped credentials" replaces "no credentials".** The sandbox holds a task-scoped coordinator token (TTL equal to the job timeout) and the job service account's metadata token, which can only call Vertex and write its own GCS prefix. Nothing else.
> - **VPC-SC depends on a Google Cloud Organization.**
>   - Access policies need one, which a plain personal project doesn't have. `bootstrap.sh` **detects whether an Organization exists**.
>   - **With an Organization:** a VPC-SC perimeter, plus `restricted.googleapis.com` and private DNS zones for `googleapis.com` and `run.app`.
>   - **Without one:** a deny-all egress firewall plus Private Google Access, IAM scoped to this project only, and no service-account permissions in other projects. ADR-0006 records the remaining risk: write access to arbitrary external Google APIs through Private Google Access, partly mitigated by the low-value service-account token.
>   - **Decided: detect, and create Cloud Identity Free if there's no Organization.** `bootstrap.sh` checks `gcloud organizations list`. If it finds none, it walks you through signing up for **Cloud Identity Free** for your domain and prints the **DNS TXT verification record** to add at your registrar, then waits until the Organization appears.
>     - The project is then created **under the Organization**, and VPC-SC (access policy, perimeter, restricted VIP, private DNS) is applied by OpenTofu.
>     - The deny-all + Private Google Access fallback is kept only for the case where Organization setup fails. In that case bootstrap stops and asks before continuing without VPC-SC.
- **Runtime:**
  - Cloud Run Job (gen2 microVM) or local Docker, with the image pinned by digest.
  - Non-root, read-only root filesystem (writable `/workspace` and `/tmp` only).
  - CPU, memory and timeout limits.
  - Locally: `--cap-drop ALL --security-opt no-new-privileges --read-only`, no Docker socket.
- **Only low-value, scoped credentials inside** (see above):
  - Input is a source **git bundle** read from GCS. Output is a result bundle + handoff written to GCS.
  - No GitHub token, no DB credentials, no app secrets.
- **Network:**
  - Cloud: all egress goes through the VPC; a deny-all internet firewall; Private Google Access to Vertex, GCS and the internal-only `coordinator` service. There are no package mirrors; dependencies are baked into the image.
  - Local: an allowlisting egress proxy container for `*.googleapis.com` only.
  - No WebFetch or WebSearch; vendored docs only.
- **Least-privilege service accounts**, so even the metadata-server token is low value:
  - writer: Vertex user, write to its own artifacts prefix, invoke the coordinator;
  - reviewer: the same, with read-only artifacts.
- **Advisory hooks inside the sandbox:** lease checks, protected paths, lint after edit, tests green before "done". Fast feedback only; not trusted.
- **Authoritative checks in the Publisher** (trusted code outside the sandbox; any failure rejects the bundle):
  - Changed paths ⊆ the leases ⊆ the **agent write-scope allowlist**.
  - **Sensitive-identifier scan:** the diff and the handoff are checked against the runtime denylist (see Data exposure) and secret patterns. No GCP project id or number, service-account emails, bucket names, owner name or email, allowlisted domains, or keys.
  - Diff ≤ 400 changed lines, excluding generated code; otherwise the task must be split.
  - gitleaks secret scan.
  - **No new** `skip`/`xfail`/`type: ignore`/`noqa`/`eslint-disable`/`@ts-expect-error`/coverage pragmas, lowered thresholds or snapshot rewrites, unless the human added an `allow:` label.
  - **Red→green check:** the acceptance tests fail on the base and pass on the head.
  - Generated code is in sync with the spec.
- **Untrusted input:** task text, issue text and seed content go through Model Armor before an agent sees them. Repo content on `main` is treated as trusted.
- **Cost:** per-task token and wall-clock budgets; `MAX_ACTIVE_TASKS=2`, `MAX_PARALLEL_WORKERS=3`; a Cloud Billing budget with alerts (OpenTofu); a kill switch on the card.

## Multi-agent system
### When to use multiple agents
The policy is in `docs/agents.md`:
- **Single agent:** size S, one area (`web` or `api`), low risk.
- **Multi-agent:** both `web` and `api`, size M or larger, or an API contract change. A typical team is a contract subtask, then backend-engineer and frontend-engineer in parallel, with the qa-expert test-first subtask before them.

### Topology
Hub and spoke, one level deep:
- The **dev-lead** plans, sends out subtasks, merges results into the integration bundle, and writes the final handoff.
- **Writers** each run as their own job/container on their own branch, in parallel up to 3.
- **Read-only specialists** (qa-expert test design, a security check of the plan) run as in-process subagents of the lead.
- **Test-first:** in multi-agent tasks, subtask 0 is always a `qa-expert` writer that adds the failing acceptance tests. Implementers' leases **exclude** those test files.
- **Coordination goes only through the internal coordinator API:** `get_task`, `claim_paths(wait_s)`, `renew_lease`, `release_paths`, `post_step`, `post_handoff`, `request_help`.
- Workers can't start further agents.

### Collision rules
Enforced in code.
1. **Path leases:**
   - Exclusive leases on glob patterns, held as Postgres rows granted atomically.
   - An overlap with any active lease, including other tasks', waits in a queue.
   - Leases have a time limit plus heartbeat; the watchdog job reclaims expired ones.
2. **Enforcement:** an advisory hook inside the sandbox, the **Publisher's authoritative check**, and a CI recheck on the PR.
3. **Single-writer hotspot locks:** `api/openapi.yaml`, `*/generated/**`, shared web modules (`web/src/lib/**`, theme/tokens). Migrations and lockfiles are outside agent scope entirely.
   - Order follows spec-first: the contract subtask goes first, the lead merges it and regenerates code, then backend and web run in parallel.
4. **Fixed base:** all writers start from the same base. Only the lead integrates, one subtask at a time with a rebase, running tests after each.
5. **Conflicts:** the lead re-dispatches the subtask on the new base instead of editing another writer's files. After 2 failures: `Blocked`.
6. **One active run per task** (unique constraint). Card moves carry an optimistic version; retries are idempotent by `run_id`.
7. **Budgets:** split across workers and enforced per job. The kill switch also releases leases.
8. **Escalation:** a lease-wait timeout, exhausted budget or `request_help` sends the task to `Blocked` and notifies a human.

### Keeping agent communication concise
Enforced by the coordinator and hooks.
1. **Strict schemas with server-side token caps.** Oversized messages are **refused**, never silently cut. Caps:
   - brief ≤ 800 tokens
   - handoff ≤ 400
   - `request_help` ≤ 200
   - findings ≤ 10 per reviewer, each ≤ 60 words
2. **Handoff fields:** `status`, `summary` (≤ 80 words), `bundle_ref`, `changed_paths`, `tests` (counts + first failing test id), `rubric_checked` (IDs), `open_questions` (≤ 3), `artifacts` (references only). No code, diffs or logs.
3. **References, not content.** Diffs, logs and traces live in GCS (locally `.artifacts/`). The lead reads them selectively, starting with `git diff --stat`.
4. **Fresh minimal contexts.** No inherited conversation. Each agent gets the brief, its lease, the relevant doc links, and the standards pack (a stable prefix, so it caches). Retries start fresh with only the findings.
5. **`post_step` goes to the UI and the database only**, never into the lead's context. The lead sees handoffs only.
6. **Output trimming:**
   - pytest `-q --tb=short`, ≤ 60 lines.
   - Lint: counts + the first 10 errors.
   - Build logs: exit status + tail.
   - Coordinator results ≤ 2k tokens, paginated.
7. **No polling:** `claim_paths` blocks on the server.
8. **Style in every agent definition:** no preamble, no restating the task, facts and decisions only.
9. **Long runs:** the lead relies on Agent SDK compaction. The plan and the handoff ledger are always re-read from `get_task`, so compaction loses nothing.
10. **Measured:** the dashboard shows "coordination overhead %". The golden eval (run on change, plus weekly) fails above 15%, or if any message hits its cap more than twice.

## Code review: cross-model (Claude writes, Gemini reviews), with aligned standards
**Why:** a reviewer from a different model family has fewer blind spots in common with the author. The risk is that the two disagree about goals or style, so alignment is designed in.

### Panel (configured in `review/panel.yaml`)
| Reviewer | Model | When | Focus |
|---|---|---|---|
| code-reviewer | **Gemini Pro on Vertex** (version pinned in config) | Always | Correctness, spec compliance, rubric |
| qa-expert | **Gemini**, plus a deterministic mutmut/Stryker run on the changed lines that `verify` executes and passes in as a number | Always | Test adequacy. The LLM interprets the mutation score; it doesn't compute it |
*(The Claude second-opinion reviewer is cut. Each review role has one model.)*
| *(security)* | Moved to the dedicated **PR security gate**, below | | |
The privacy, performance and UI/UX specialist reviewers are **cut**. Their checks are covered deterministically instead:
- PII and exposure: the SDP and denylist scans, the PR security gate.
- Performance: Lighthouse budgets, query-count assertions in tests.
- UI: Playwright e2e and axe accessibility checks in CI.

### Harness
- **Gemini reviewers** run **Gemini CLI in headless mode** in the review sandbox.
  - Read-only: write and shell tools are excluded apart from allowlisted test and benchmark commands.
  - Same coordinator API and same egress rules.
  - Output must match the shared findings JSON schema. Headless flags and the output format get checked against the current Gemini CLI docs at build time.
- **Claude reviewers** use the Agent SDK with the read-only agent definitions.
- **Model Armor** sanitises Gemini traffic too. Gemini's cost is logged to `llm_calls` with `provider=vertex-gemini`.

### Alignment guarantees (no clash between writer and reviewer)
- **One standards pack, identical bytes for both sides.**
  - `AGENTS.md` + `docs/standards/*` (architecture, coding, testing, definition of done, `review-rubric.yaml`) + `docs/standards/exemplars/`.
  - Assembled by code into one block. Writers (Claude) and reviewers (Gemini and Claude) get **exactly the same block**.
  - Its SHA-256 is recorded in the writer's handoff and in every review. If the hashes differ, the review is invalid and re-runs.
- **Pinned version.** The pack is always taken from the **base commit**, never the PR head. Agents can't edit it anyway (protected paths), so both sides judge against the same version.
- **Agent files hold no standards.** Agent definitions follow a fixed template with these sections only: `Role`, `Scope`, `Process`, `Output`, `References`. A `check.sh` lint checks the template structure and that `References` points to the pack. It doesn't search for words like "always" or "never", which would be brittle. A CODEOWNERS entry puts every change to an agent file in front of a human reviewer. The Gemini prompts generated from these files inherit the same structure.
- **Rework base:** each rework round starts from the **previous head**, with only the blockers as input. The review always diffs against the original base.
- **Shared exemplars.** `docs/standards/exemplars/` holds small canonical files that both sides are told to match: an API route, a domain service, an adapter interface, a Pydantic model, a React component, a hook, a unit test, a property test, a Playwright test. Rubric items link to them, with ✅/❌ snippets, so a rule has one reading.
- **Formatting never reaches review.**
  - The writer's hook auto-formats (ruff format, Biome).
  - The Publisher **rejects** an unformatted bundle (`ruff format --check`, `biome check`).
  - The reviewer only ever sees formatted, lint-clean, type-clean code, and is told those categories are out of its scope. Findings tagged with them are dropped automatically.
- **Structure is machine-checked.** import-linter (Python layers: `api → domain → adapters` interfaces only) and dependency-cruiser (web: `app → features → components → lib`) encode the architecture rules. Neither model has to judge layering by eye.
- **Drift is measured.** Calibration also tracks:
  - the **style/structure NIT rate** per reviewer (target: trending toward 0);
  - **repeat NIT patterns**, auto-grouped. Three or more of the same kind triggers a rubric proposal, so recurring disagreements become an explicit rule or an explicit "not a rule".

### Alignment: one standards pack, used by both
1. **`AGENTS.md` is the single source of truth.** `CLAUDE.md` imports it (`@AGENTS.md`), and Gemini CLI is pointed at it (`contextFileName: "AGENTS.md"`). Both models read identical text.
2. **`docs/standards/`** covers architecture rules (layering, adapters, error model), coding guidelines, testing policy, definition of done, and **`review-rubric.yaml`**. Every rubric item has an ID (e.g. `ARCH-03 domain code imports no adapters`, `TEST-02 every bug fix adds a regression test`) and a check method: tool, test or judgement.
3. **Mechanical style is left to tools, never to LLM review.**
   - ruff, Biome, pyright/tsc strict.
   - **import-linter** (Python layering) and **dependency-cruiser** (web) turn architecture rules into code.
   - Findings in tool-covered categories are dropped automatically.
4. **One checklist on both sides.**
   - The writer self-checks against the rubric and lists `rubric_checked` IDs in its handoff.
   - A **blocking** finding must cite a rubric ID, a spec clause, a failing test, or a concrete bug with a reproduction.
   - Anything else is a non-blocking **"rubric proposal"** queued for a human to accept into the rubric, so standards evolve on purpose instead of drifting.
5. **Isolated reviewer context.** The reviewer job's input is built by code from exactly:
   - the **git diff**;
   - the **requirements**: the task's acceptance criteria plus any spec clauses the diff touches;
   - **the full standards pack** (the same hashed block the writer got: `AGENTS.md`, `docs/standards/*`, exemplars);
   - the CI pass/fail summary, as facts.

   It **never** receives the writer's steps, reasoning, handoff summary or `post_step` events, and a contract test asserts that. The reviewer judges the outcome, not the author's story.
6. **Two severity tiers only:**
   - **`BLOCKER`:** bugs, security flaws, broken invariants or contracts, failing or missing required tests. Must cite a `rubric_id`, `spec_ref`, `test_id` or a concrete repro; otherwise the merge step downgrades it to `NIT`.
   - **`NIT`:** style, naming, readability, suggestions. Posted as non-blocking PR comments and never triggers a rework run.
   - Findings schema: `severity`, `citation`, `file:line`, `evidence`, `fix`. Merged and de-duplicated **by code** (file:line + category), not by another LLM call.
7. **Auto-approve on zero blockers.** Once the merged findings contain no `BLOCKER`, the orchestrator passes Agent review automatically and moves the card to `Human review`; human merge is still required.
8. **Capped loops and deadlock escalation.**
   - At most **2 rework rounds** (`MAX_REVIEW_ROUNDS=2`, configurable up to 3). Each rework starts fresh with only the blockers.
   - The writer may `dispute` a blocker once, citing the rubric or spec.
   - These go to `Human review` with both positions in ≤ 100 words each, instead of spending more tokens:
     - a blocker still open after the cap;
     - an unresolved dispute;
     - a cross-model conflict: the Gemini reviewers and the Claude security gate contradict each other on the same code, e.g. one requires a change the other blocks, or the writer (Claude) disputes a Gemini blocker on design grounds;
     - the same blocker coming back after a fix.

   The card is flagged `needs decision`.
9. **Calibration (weekly + whenever the rubric, agent files or reviewer model change):**
   - A golden review set of seeded-bug diffs and clean diffs.
   - Tracks each reviewer's recall and false-positive rate, Claude↔Gemini agreement, and how often writers break rubric items.
   - A drop below threshold fails the eval and alerts.
   - Shown on the dashboard as "reviewer agreement" and "review precision".

## Model routing and open-weight models (cost)
- **`ai/models.yaml`** maps each feature to `{provider, model, effort, fallback}`. `adapters/llm` is provider-neutral:
  - `AnthropicVertex` for Claude.
  - The Vertex Model Garden serverless (MaaS) endpoint for open models, billed per token with no GPU endpoints.
  - Gemini via Vertex for the reviewers.
- **Open-weight features:**
  - **Nightly digest + weekly rollup summaries.**
  - **Rejection analysis / patterns + follow-up drafts.**
  - **Served from Vertex AI Model Garden**, using its serverless, pay-per-token (MaaS) open models, e.g. gpt-oss, Llama 4 or Qwen3. There are no self-deployed GPU endpoints, so nothing costs money while idle.
    - The model is **pinned by eval** in M1 and recorded in `ai/models.yaml` with its Model Garden id and region. Each of these features moves from Claude Sonnet to it.
    - Calls go through the same Vertex auth (service account / ADC), the same VPC-SC perimeter and the same `llm_calls` logging (`provider=vertex-maas`).
    - `bootstrap.sh` checks access with a test call. If the model needs Model Garden enablement or terms, it prints the console link, the same way as the Claude check.
- **Everything else stays on Claude:** prescreen and extraction on Haiku 5.5, kits, chat, research and triage. Dev, review and security agents are excluded from open-weight.
- **Guardrails for open models:**
  - Same Model Armor + SDP + redaction path.
  - Strict Pydantic validation with one retry, then **automatic fallback to `claude-sonnet-5-5`** (logged as `fallback=true`).
  - A **per-feature eval gate**: the open model must score within 5 points of the Claude baseline on the feature's labelled set, re-checked nightly. Failing the gate flips routing back to Claude automatically and alerts.
- **Dashboard:** cost per feature by provider, fallback rate, eval score by provider.

## GitHub workflow triggers (second review fix)
PRs opened with `GITHUB_TOKEN` don't trigger new workflow runs, so required checks hang on the push instead:
1. **`ci.yml`** runs on `push` to `agent/**`. Pushes made with the deploy key **do** trigger workflows. It has no secrets and no GCP credentials. It re-runs lint, types, tests and **red→green independently**.
2. **`security-review.yml`** is triggered by **`workflow_run`** when `ci.yml` completes.
   - It runs **from `main`'s workflow definition** and treats the agent branch's diff **as data only**: fetched with `git diff`, never checked out or executed.
   - It gets Vertex-only credentials from the **review** WIF provider.
   - It posts its result as a **check run on the head SHA** (Checks API, `checks: write`).
3. **`agent-pr.yml`** opens the PR. Its body is built from **structured fields only** (task id, card link, the signer's verdict summary), with no agent free text.
4. **Branch rules:** required checks on `main` are `ci` + `security-review`. They're attached to the head SHA, so they apply to the PR regardless of which event created them.
5. **Approvals:** agent PRs need 1 approval from you (the PR author is the `github-actions` bot). Your own PRs need the required checks to pass, with you as the ruleset **bypass actor for the approval rule only**; a solo owner can't approve their own PR.
6. **Shared round counter:** the app records `review_rounds` per task. The in-app review and the PR security gate, via a `check_run` webhook, both add to it, so the 2-round cap covers both.

## Streaming + redaction (second review fix)
- **Fast path, every chunk:** secret-pattern and denylist checks over a **sliding window** (the last 256 characters plus the new chunk), so a value split across chunks is still caught.
- **Segment path:** SDP + Model Armor run once per sentence or paragraph segment, and the UI shows segments as they're released.
  - If a released segment is flagged later, a `retract` SSE event replaces it with `[redacted]`.
  - Agent steps are released per step, already redacted.

## PR security gate (a separate security review agent)
- **Where:** GitHub Actions `security-review.yml`, run on **every PR**, agent and human. It's a **required status check** on `main`, independent of the in-app review panel. Before GitHub exists, `check.sh --security` runs the same thing locally.
- **Deterministic layer first:**
  - Semgrep (OWASP + custom rules for this repo's auth and redaction patterns), CodeQL.
  - gitleaks + the denylist scan, pip-audit / pnpm audit.
  - Trivy on built images, Checkov/Conftest on any IaC diff.
  - Results go to the agent as a short summary.
- **Agent layer:**
  - The `security-auditor` agent (adapted from job-seeker) runs read-only via Claude Code headless on Vertex (review WIF provider, `claude-opus-5-5`). It's the only model in the gate; there's no Gemini pass.
  - Cross-model diversity still comes from the in-app review (Gemini) vs the security gate (Claude).
  - Isolated context: the PR diff, `AGENTS.md`, `docs/standards/security.md`, the threat model (`docs/security.md`) and the deterministic results only.
  - Focus areas:
    - authorization on every new or changed route (row-level security + role checks);
    - input handling and injection, including prompt injection paths into LLM calls;
    - SSRF and egress;
    - **data exposure** (secrets, project identifiers, PII in responses, logs or SSE);
    - redaction-filter coverage of new outputs;
    - unsafe dependencies.
- **Output:** the same BLOCKER/NIT schema. Any BLOCKER fails the check.
  - Findings are written to the job summary and to the card **through the redaction filter**, with file:line and the category, never the secret value.
  - For agent PRs, a failed gate counts as one review round, so it sits under the same 2-round cap and escalation rules.
- **No overlap:** the in-app panel no longer runs a security reviewer, so security tokens aren't spent twice.

## Adversarial testing agent (scheduled, prod, tester account only)
- **When:** Cloud Scheduler → Cloud Run Job `redteam`, weekly in low-traffic hours, plus on demand by the owner. It's behind the flag `REDTEAM_ENABLED`, with a kill switch.
- **Identity:** only the **tester account**.
  - A **launcher** job runs no agent code. It mints the tester's custom token, exchanges it for a short-lived ID token (≤ 1 h) and starts the agent job with only that token.
  - The **agent's service account has Vertex access only**, and no token-creator or `signBlob` permission, because IAM can't restrict minting to one uid.
  - The tester's data lives in the **same tables, under the same row-level security** as everyone else, so IDOR probes test the real boundary.
  - `tester-reset` calls a `SECURITY DEFINER` Postgres function that is hard-coded to delete and re-seed rows for the tester's uid only. The job's DB role can execute that function and nothing else.
  - The M0 spike checks that custom-token ID tokens carry `email` / `email_verified`. If they don't, the launcher sets developer claims and the API's check accepts them only for the tester uid.
  - The tester tenant holds **synthetic seed data only** and is reset before and after every run.
- **Agent:** Claude Agent SDK (`claude-opus-5-5`), with a Gemini variant on alternate weeks for diversity. Its definition is `.claude/agents/redteam.md` (new; draws on `security-auditor` + `ui-ux-tester`).
  - **Tools:** an HTTP client and Playwright allowed to reach **only** the app's own `run.app` URLs; nothing else.
  - **Egress:** the app URLs + Vertex only (same deny-all firewall as the sandbox). The agent's only credentials are its Vertex-only service account and the tester ID token from the launcher.
- **Playbooks** (versioned in `redteam/playbooks/`):
  - Prompt injection via ad text, chat, task descriptions and uploads.
  - Model Armor bypass attempts.
  - Authorization: IDOR against **canary ids**; tester → maintainer escalation (triggering agents, `/docs`); JWT tampering; replay with an unverified email.
  - Data exposure probes (errors, `/healthz`, SSE, the bundle) for secrets, project identifiers, PII.
  - Rate-limit and abuse checks at a capped volume.
  - XSS/CSRF and CSP checks in the UI.
- **Rules of engagement** (enforced by the harness, not just the prompt):
  - The tester identity only, inside the tester tenant only.
  - ≤ 1 request/second and ≤ N requests per run; no load or DoS tests.
  - No attacks on GCP control-plane or metadata endpoints, nothing outside the app URLs.
  - Token and time budget.
  - **Stop on first confirmed cross-tenant read:** the owner tenant holds **canary records** with marker strings. If the red team ever sees one, it stops at once and records the finding **without storing the content**, and the owner is paged.
  - No bypass header: red-team traffic gets exactly the same protections. It's only labelled in telemetry by the tester's uid.
- **Findings:**
  - Structured (severity, playbook, endpoint, a minimal redacted repro) and stored in an owner-only Security view.
  - High severity → a Cloud Monitoring alert.
  - In-scope (web/api) fixes are filed as **draft** Backlog cards labelled `security`. They're built from structured fields only (category, endpoint, rubric id), with no free text from the agent and no payloads. A human must review and edit a draft before triage can run, so the red team can't become a prompt-injection path into the dev agents.
  - Every confirmed finding becomes a **regression test or eval case**, so the next run and CI check it.
- **Dashboard:** attacks attempted vs blocked by layer (Model Armor, row-level security/role, rate limit, redaction), open findings, time to fix.

## Agent definitions (`.claude/agents/`, adapted from job-seeker)
- **Shared by humans and sandbox agents.** Each file is 80–120 lines and points to `AGENTS.md` / `docs/standards`. Model IDs come from `ai/models.yaml`, checked in the M0 spike.
- **Reviewers are read-only.** Job-seeker's `code-reviewer` had Write/Edit.
- **Gemini reviewer prompts** are generated from the same agent files (the body after the frontmatter) into `review/gemini/*.md` by a script. A CI check keeps the copies in sync.
- **Changes to agent files or the rubric** require the golden-task and calibration evals to pass.

| Agent | From job-seeker | Role | Model |
|---|---|---|---|
| dev-lead | `multi-agent-coordinator.md` (rewritten) | Plan, dispatch, integrate | claude-opus-5-5 |
| backend-engineer | new | Python/FastAPI + tests | claude-opus-5-5 |
| frontend-engineer | new, with `ui-designer.md` merged in | Next.js, board, dashboard | claude-opus-5-5 |
| qa-expert | `qa-expert.md` | Writes the test-first subtask; reviews test adequacy (Gemini) | claude-sonnet-5-5 / Gemini |
| prompt-engineer | `prompt-engineer.md` | Prompts in `domain/` + eval cases (new eval files only) | claude-sonnet-5-5 |
| documentation-engineer | `documentation-engineer.md` | `docs/` (not `docs/standards/`) | claude-haiku-5-5 |
| code-reviewer | `code-reviewer.md` | Review (read-only) | Gemini |
| security-auditor | `security-auditor.md` | **PR security gate** (read-only, in Actions) | claude-opus-5-5 |
| redteam | new (from `security-auditor` + `ui-ux-tester`) | Scheduled adversarial testing in prod, tester account only | claude-opus-5-5 / Gemini alternating |
| ui-ux-tester | `ui-ux-tester.md` (Playwright replaces Chrome automation and computer use) | **Human-only** in the dev harness (UI checks while building); not in the review panel | claude-sonnet-5-5 |

`performance-engineer` and `gdpr-ccpa-compliance` are kept as **human-only** harness agents, not part of the automated review.

**Human-only** (kept in `.claude/agents/` for building the app in Claude Code, but never sent out by the dev-lead): `devops-engineer`, `cloud-architect`, both adapted from job-seeker.

Not used: `gtm-strategist`, `seo-specialist`, `aiso-specialist`, `legal-advisor`, `tech-recruiter-melbourne`. The last one could become a later in-app "recruiter view".

**Visibility:**
- A run tree (lead → writers → Publisher → reviewers) with live steps, leases, budget and status.
- A lease map view.
- Dashboard KPIs: pass rate (single vs multi), parallelism, lease wait, coordination overhead, reviewer agreement, cost per task.

## Seed data (dummy, derived from job-seeker)
- **Source:** `job-seeker/data/jobradar.db`, opened **read-only**: 13,162 roles, 1,345 events, 7,759 llm_calls, 60 targets, 579 contacts, 62 chats, 13 rejections.
- `scripts/build_seed.py` runs on your machine only and writes `seed/*.jsonl` for you to review.

| Table | Treatment |
|---|---|
| roles | ~1,500 sampled, stratified by source, status and score; dates shifted to be relative to now; ad text de-identified by SDP and shortened to summary + requirements; synthetic ids and URLs; consistent fake company names (`--keep-companies` for local use only) |
| role_events | Shape and timing of transitions kept |
| llm_calls | Feature, model, tokens, cost, time only; prompts and outputs dropped |
| targets | Fake names, with sector and size kept |
| contacts, chats, rejections, followups, campaign, experience | Fully synthetic, from a fictional persona |
| Your profile, CVs, `roles.json` answers | Excluded |

- **Gate:** an SDP scan of `seed/` (in `check.sh`, later in CI) fails on any PII.
- **Loading:** idempotent, via `dev.sh` locally and a `seed` job in prod.
- **Dev-board seed:** ~20 cards and 3 recorded agent runs.

## Agent tools
1. **In-app agents** (triage, research) on Vertex call domain services directly through tool-runner tools (ADR-0004).
2. **Sandbox agents** (Claude via Agent SDK, Gemini via Gemini CLI) reach the app only through the internal `coordinator` service: a small HTTP API with strict schemas, using task-scoped tokens. Each runtime gets a thin tool wrapper over it. GitHub access moved to the Publisher.
3. There's no external tool server for the app (ADR-0009).

## Security & data protection
- **Model Armor:**
  - `sanitizeUserPrompt` runs on every untrusted input: ads, pasted text, uploads, task text, tool and web results.
  - `sanitizeModelResponse` runs on outputs shown to users.
  - Fails closed. Applies to both Claude and Gemini.
- **SDP:**
  - De-identify PII before prompts (deterministic tokenisation).
  - Redact `llm_calls` and logs.
  - Inspect uploads.
  - Run the seed gate.
### Access control
- **No self-registration.** Identity Platform has user sign-up turned off (`disabled_user_signup = true` in OpenTofu), and there are no anonymous accounts. The UI has a sign-in page only, with no "create account" link.
- **Exactly two accounts:** the owner (`maintainer` role) and a tester (`user` role, so the non-maintainer paths can be tested too).
  - Created by `scripts/provision_users.py` (Firebase Admin SDK), run by you once after the initial deploy.
  - It reads the two emails from Secret Manager (`APP_USERS`), never from the repo.
  - Creates the users with no password and triggers Identity Platform's own **password-reset email** through the REST `sendOobCode` endpoint; the Admin SDK only generates links and doesn't send them. Email verification is required, and TOTP MFA is enabled for the owner.
  - The script refuses to create anyone not in `APP_USERS`.
- **Enforced in three places:**
  1. An **Identity Platform blocking function** (`beforeSignIn`; `beforeCreate` always refuses) allows sign-in only for an **exact, lower-cased email match** with `APP_USERS` and `email_verified = true`.
  2. **The API checks it again on every request**: the JWT's `email` and `email_verified` against the same allowlist, plus the role from the custom claim. This is defence in depth if the blocking function or console settings drift.
  3. **Locally**, fake auth has the same two accounts, with placeholder emails from `~/.config/jobradar/users.json`.
- **Generic refusal message** ("This account can't sign in"). It never reveals which accounts exist.
- **Postgres row-level security** by `user_id`. Only `maintainer` can trigger dev agents or view `/docs`.
- **Tests:**
  - A sign-up attempt is refused.
  - A valid JWT for a third email → 403.
  - A valid JWT with `email_verified=false` → 403.
  - The tester can't trigger agents.
  - `provision_users.py` refuses unknown emails.
  - Drift check: a nightly job lists Identity Platform users and alerts if there are any accounts beyond the two.
- **Edge:**
  - Cloud Run `run.app` URLs, HTTPS by default; no load balancer or custom domain, so there's no DNS step and no owner domain exposed.
  - Every route requires auth except the sign-in page and `/healthz`.
  - Rate limiting in the app: per-user and per-IP token buckets in Postgres, with stricter limits on AI and agent endpoints.
  - Cloud Run `max-instances` caps.
  - Security headers: CSP, HSTS, frame-ancestors.
  - The `api` service accepts requests from `web` and the coordinator audience only; the coordinator path is internal-ingress-only for sandbox traffic.
  - ADR-0005 records the decision not to use Cloud Armor (2 users, sign-up off, cost) and how to add LB + Cloud Armor later.
- **Secrets:** Secret Manager; no service-account keys (Workload Identity Federation). The deploy key's private half lives only in Secret Manager and is read only by the Publisher's service account.
- **Supply chain:** SLSA provenance, Artifact Analysis scanning, digest-pinned images, lockfiles.
- **Data hygiene:** never port job-radar's `data/`, `profile/`, `.env` or `roles.json`.

### Data exposure: no keys, project details, owner details or PII
**Sensitive identifiers:**
- Kept out of the repo: GCP project id and number, region-specific resource names, service-account emails, bucket names, billing account, owner name, **both account emails and their domain**.
- `infra/envs/prod/*.tfvars` is **untracked**. Values come from GitHub Actions secrets and variables (CI) and from `~/.config/jobradar/` (local).
- A **runtime denylist** built from these values, stored in Secret Manager, feeds the filters below.
- `check.sh` and CI run gitleaks plus a custom rule that scans for denylisted values, so the repo stays clean.
- The initial commit uses your **GitHub noreply email** as the author address.

**Output redaction filter.** One shared module applied to **every user-facing output**:
- API responses, SSE (agent steps, kits, chat), coordinator results, review findings, PR titles, bodies and comments (via the Publisher), dashboard data.
- It runs, in order: secret patterns (gitleaks rule set) → denylisted identifiers → SDP PII inspection → Model Armor `sanitizeModelResponse`.
- Matches are replaced with `[redacted]`.
- For agent steps it **fails closed**: if the filter errors, only the step type is shown, never the raw content.

**API surface:**
- RFC 9457 problem responses with no stack traces, SQL or internal hostnames; correlation ids only.
- `/healthz` returns `{"status":"ok"}` only, with no versions, project or region.
- `/docs` and `/openapi.json` are maintainer-only in prod.
- LLM spend shows in the dashboard as app metrics; billing and project details never appear.

**Frontend bundle:**
- Only public-by-design config: Identity Platform web API key, restricted by HTTP referrer and to the Identity Toolkit API only.
- No source maps in prod.
- CI scans the built bundle for secret patterns and the denylist.
- No owner name in the UI or metadata.

**Sandbox:**
- No secrets, denylisted values or user data in the environment. Seed data only, already free of PII.
- Even if an agent reads the project id from the metadata server, it can't leave the sandbox: the Publisher scan rejects the bundle and the redaction filter cleans the steps.
- Agents receive no other users' data. Tasks are de-identified by SDP.

**PII:**
- Users see only their own data (row-level security).
- Shared areas (the dev board, run trees, findings) are SDP-redacted.
- Logs are redacted.
- Seed data passes the SDP gate.
- Web analytics stay in the app's own Postgres. No cookies, no raw IPs, and events carry no emails, names or free text.

**Canary tests:**
- E2E and nightly runs plant fake canary secrets, a fake project id and a fake PII record in the sandbox environment, the task text and the DB.
- They assert none of these reach the UI, SSE, API errors, the frontend bundle, the PR body or the logs shown to users.
- **Refusals:** the client-side `BetaRefusalFallbackMiddleware` (Vertex has no server-side fallbacks).

## Observability
- **Backend:** OTel → Cloud Trace; JSON logs → Cloud Logging; Error Reporting; SDK spans for each LLM call.
- **Uptime and SLOs:** uptime checks on `web` and `api /healthz`. SLOs: 99% availability (best effort on `f1-micro`); p95 < 300 ms on non-AI endpoints; time to first token < 3 s on AI endpoints. Burn-rate alerts.
- **Log-based metrics:** LLM cost by provider and feature, Model Armor blocks, agent outcomes, Publisher rejections by rule, job failures. Everything is defined in OpenTofu.
- **Web analytics: built into the app** (no extra service).
  - A small typed client in `web` batches events to `POST /api/events` (part of the spec, rate-limited, schema-validated, no free text): page views (route template only, never ids), product events (kit generated, card moved, agent run started/approved) and Core Web Vitals (LCP, INP, CLS via `web-vitals`).
  - Stored in Postgres as `web_events`, with session ids rotated daily, no cookies, no IPs and no user ids beyond a role tag. Kept for 90 days, purged by the rollup job.
  - Shown in an **Analytics** tab of the in-app dashboard: views by page, funnels, Web Vitals p75 by page against budgets. Vitals p75 also feeds a log-based metric with an alert.
- **Performance gates:** Lighthouse CI budgets; a k6 smoke test on the canary's tagged URL before promotion.

## Architecture (GCP, prod only)
> **Revised after independent review:**
> - **Separate `coordinator` Cloud Run service** with internal-only ingress for sandbox traffic. Cloud Run ingress is set per service, so this can't be a path on the public `api`.
> - **Database connections:** `db-f1-micro` allows about 25 connections. There's **one shared `LISTEN` connection** per `api` instance, fanning out to SSE clients in memory. App pools are capped (`api` 5, jobs 2). `max-instances` limits are set so the total stays under 20. The SLO target for this tier is lowered to **99%** and documented as best effort, with no SLA.
> - **Feed replay runs hourly**, scoring at most 20 roles per run, with a daily LLM spend cap per feature that stops jobs when it's reached.
> - **No Cloud Tasks emulator** exists, so locally the Tasks adapter runs work in-process through an asyncio queue behind the same interface. The real Cloud Tasks is used in prod.
> - **Reaching `run.app` from deny-all jobs** (the red team): to be verified in M0. If Private Google Access doesn't reach `run.app`, the red team calls `api` through a private (internal) address route. Cloud NGFW FQDN rules are avoided for cost.
- **Cloud Run services:** `web`, `api`, `coordinator` (internal-only ingress).
- **Cloud Run function:** `auth-blocking` (Identity Platform `beforeCreate` / `beforeSignIn`).
- **Cloud Run Jobs:**
  - App: `feed-replay`, `digest`, `rollup`, `watchdog` (leases + Identity Platform user-count check), `evals`, `migrate`, `seed`, `provision-users`.
  - Agents: `agent-lead`, `agent-writer`, `agent-reviewer`.
  - Publisher: `test-runner`, `signer`, `push`.
  - Red team: `redteam-launcher`, `redteam`, `tester-reset`.
- **Model Armor region:** checked in the M0 spike. If it isn't available in `australia-southeast1`, the nearest supported region's endpoint is used.
- **Cloud SQL Postgres 16** `db-f1-micro`: private IP via Private Service Access, Direct VPC egress, IAM DB auth, backups + point-in-time recovery, `LISTEN/NOTIFY` for SSE.
- GCS (uploads, PDFs, bundles, artifacts), Cloud Scheduler, Cloud Tasks, Secret Manager, KMS (verdict signing + OpenTofu state encryption), Artifact Registry (images only).
- Region `australia-southeast1`; Vertex on the `global` endpoint for Claude, and Gemini in a supported region.
- **Deploys are canaries:** 10% → smoke + SLO check → 100%, with automatic rollback. Feature flags (`MULTI_AGENT`, `CROSS_MODEL_REVIEW`, `AGENTS_ENABLED`).

## Repository, commits and the GCP bootstrap
- **No git commits until you ask.**
  - The repo folder `job-radar` already has `.git` (initialised by the owner). No commits until the owner asks, and nothing touches GitHub before the initial commit.
  - Local gates run through `scripts/check.sh`, the same script CI calls later, plus Claude Code hooks.
  - `.pre-commit-config.yaml` is written but only activated after `git init`.
- **Local dev-board agents before GitHub:** with `dev.sh --with-agents` (opt-in), `LocalGitHost` snapshots the working tree into a **scratch bare repo outside the project** (`~/.cache/jobradar/agent-remote`). Agent branches and "PRs" (shown in the app's own diff view) live there, and the project folder's git is never touched. After the initial commit, `CodeHost` switches to `GitHubHost`.
- **Bootstrap:** one command, `scripts/bootstrap.sh`, run by you before the initial commit. It's idempotent and safe to re-run.
  1. Checks the tools (gcloud, gh, tofu, uv, pnpm, docker).
  2. **Interactive sign-ins (unavoidable):** `gcloud auth login` + `gcloud auth application-default login`, and `gh auth login`, each only if not already signed in.
  3. Lists your billing accounts (you pick one if there's more than one) and writes the untracked `~/.config/jobradar/prod.tfvars`. It prompts once for the two account emails, which go into the secret `APP_USERS` and the denylist.
  4. `infra/bootstrap/` (OpenTofu, local state, then moved to GCS):
     - **Creates the GCP project** (random suffix), links billing, enables APIs.
     - Creates the state bucket, the WIF pool and provider bound to the repo, the deploy service account, Artifact Registry and the billing budget with alerts.
     - Via the GitHub provider: creates the **private** repo (on your **GitHub Pro/Team** plan), the `prod` environment with you as required reviewer, and the Actions variables. The deploy key and webhook are registered by `bootstrap.sh` (see below). Rulesets come after the first push.
  5. **Vertex Claude access check:** a test call to `claude-opus-5-5` and `claude-sonnet-5-5`. If Model Garden enablement or quota is missing, it prints the direct console link and waits. This is the **only likely manual click**.
  6. Checks Gemini access the same way; it normally needs no enablement.
  7. Prints a summary. The next step is the initial commit, when you ask.
- **Revised after independent review:**
  - **OpenTofu client-side state encryption** with a KMS key provider, on both the bootstrap and prod state.
  - **Environment required reviewers on a private repo** may need a higher plan than Pro/Team; this is checked in the M0 spike. If they aren't available, the `apply` job runs only through `workflow_dispatch` triggered by you, plus a ruleset on `main`.
  - **No secrets in tofu state.** The deploy key and the webhook HMAC are generated by `bootstrap.sh` outside OpenTofu (`ssh-keygen` / `openssl`). They're written straight to Secret Manager and GitHub through `gcloud` / `gh`, and tofu refers to them only by name (or uses OpenTofu ephemeral or write-only attributes where the provider supports them).
  - **Rulesets come after the first push.** Bootstrap creates the repo without the "PR required on `main`" ruleset. `deploy.yml`'s final step, or `bootstrap.sh --finalize`, applies the rulesets once the initial commit has landed, so they don't block the initial commit's own push.
  - **GitHub plan (decided): private repo + GitHub Pro or Team.**
    - The M0 spike checks which tier your account needs for rulesets, environment required reviewers and CodeQL / code scanning. Code scanning on private repos may need GitHub Advanced Security / Code Security as a paid add-on.
    - If code scanning isn't available on your plan, Semgrep in the PR security gate covers static analysis, and CodeQL is dropped.
    - `bootstrap.sh` stops with a clear message if the account's plan can't support the rulesets.
- **What's still manual, and why:** if the domain has no Organization, a one-time Cloud Identity Free sign-up plus a DNS TXT record at your registrar (needed for VPC-SC); the browser sign-ins (GCP and GitHub require them); choosing a billing account if you have several, possibly the Claude enablement and terms in Model Garden, and each user setting their password from the emailed link.
- **Initial commit (only when you ask)** → push to `main` → `deploy.yml`:
  1. `check.sh`
  2. `tofu plan` (shown in the job summary)
  3. **environment approval**
  4. `tofu apply`: network + VPC-SC perimeter, Cloud SQL, Run services (`web`, `api`, `coordinator`) and jobs, Identity Platform (sign-up off, TOTP, blocking function), Model Armor/SDP templates, KMS verdict key, Scheduler, monitoring/SLOs/uptime/alerts.
  5. Build + sign images
  6. `migrate` job
  7. Deploy (canary)
  8. `seed` demo tenant + **`provision-users` job** (creates the two accounts from `APP_USERS` and sends password-setup emails; does nothing if they already exist)
  9. Smoke test + uptime checks green

  `tofu test` and a dry-run `plan` against a mock provider run locally in `check.sh` beforehand, so the first apply isn't the first test.

## Repo layout
```
AGENTS.md  CLAUDE.md(@AGENTS.md)  .claude/{settings.json,agents/}  .devcontainer/
scripts/{dev.sh,check.sh,bootstrap.sh,build_seed.py}   docker-compose.yml
api/openapi.yaml .spectral.yaml README.md
backend/src/app/{api,domain,adapters,jobs,agents,guards,generated}/  migrations/  tests/{unit,property,contract,integration,evals}/
sandbox/{writer,reviewer-claude,reviewer-gemini}/   publisher/   review/{panel.yaml,gemini/}
web/   infra/{bootstrap,modules,envs/prod,policy,tests}/
docs/{standards/{architecture.md,coding.md,testing.md,definition-of-done.md,review-rubric.yaml},agents.md,deployment.md,bootstrap.md,security.md,ai-features.md,ui-ux-spec.md,adr/}
.github/{workflows,CODEOWNERS,renovate.json}   seed/
```

## Porting from job-radar
| Source | Destination | Change |
|---|---|---|
| `llm.py` (`parse_call`, `system_blocks`, logging, spend) | `adapters/llm` | `AnthropicVertex`, guard + PII hooks, Postgres logging, a provider field |
| `scoring.py`, `kits.py`, `evidence.py`, `rejections.py`, `followups.py` | `domain/jobs` | Per-user profiles, no personal prompts |
| `chat.py` | `domain/jobs/chat` | SSE |
| `targets.py` | `agents/research` | A visible multi-step agent (basic `web_search_20250305`) |
| `scrapers/*` + fixtures | `adapters/scrapers` | `JobSource`; fixtures only; `SeedFeedSource` for the demo |
| `scheduler.py`, `pipeline.py` | `jobs/` | Cloud Run Jobs; APScheduler only locally |
| `schema.sql`, `DATA_MODEL.md` | Alembic + SQLAlchemy 2 | `user_id` + row-level security |
| `tests/*` (237) | `backend/tests` | Ported with each module; network block kept |
| `KIT_SPEC.md`, `UI_SPEC.md` | `docs/` | Generalised |

Not ported: Windows-specific code, `cv_patch.py`, the vanilla JS frontend.

## Local run: `scripts/dev.sh`
1. Checks prerequisites.
2. `docker compose up -d` (Postgres, fake GCS, egress proxy). Cloud Tasks runs on an in-process queue locally.
3. `uv sync`, migrate, seed.
4. Generates the client from the spec.
5. Runs api + web + local cron.

**Modes:**
- `LLM=replay|vertex` (replay = offline)
- `GUARDS=fake|gcp`
- `REVIEW=claude-only|cross-model`
- `--with-agents` (Docker sandbox + LocalGitHost)
- `--reset-db`

Runs in bash (Git Bash or WSL on Windows).

## Engineering harness
- **Test-first:**
  - Humans: tests are approved before code.
  - Agents: test-first subtask + the Publisher's red→green check.
  - Agents may not edit existing tests.
- **Gates (in `check.sh`):**
  - ruff, pyright strict, import-linter, Biome, tsc strict, dependency-cruiser.
  - gitleaks; Spectral + oasdiff + generated-code sync check.
  - pytest/Hypothesis/Testcontainers, Schemathesis, Vitest, Playwright.
  - `tofu fmt/validate/test`, Checkov, Conftest.
  - SDP seed scan.
- **Nightly (cheap):** scoring, triage and open-model gate evals; the Model Armor injection set.
- **Weekly + on change (expensive):** mutmut/Stryker full run, dev-agent golden tasks, reviewer calibration, coordination-overhead budget, red team.
- **Claude Code hooks** (for building it): lint/typecheck after edits, the Stop hook blocks while tests fail, refusal of edits to generated code and protected paths.

## Build order (milestones)
> **Revised order after independent review:** GCP risk comes first.
> - **M0** also runs `bootstrap.sh`: the project, quotas (**request the Vertex Claude quota on day 1**), and model access checks. It includes a **verification spike** on every item in "Things to verify" below.
> - **Walking skeleton after M1:** `tofu apply` run from your machine with your own credentials (no commit, no GitHub). It deploys `web` + `api` + Cloud SQL, proves private IP, Identity Platform sign-in with sign-up off, Model Armor and Vertex calls, and is then left running.
> - M5/M6 then only add CI/CD on top of infrastructure that already works.
>
> **Things to verify (M0 spike, before relying on them):**
> - `BetaRefusalFallbackMiddleware` in the Python SDK, and Vertex's lack of server-side fallbacks.
> - `web_search_20250305` on Vertex.
> - Model IDs and Vertex availability for `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`.
> - The Gemini CLI headless mode, JSON output and context-file setting name (`contextFileName` vs `context.fileName`).
> - Gemini and MaaS open-model availability in the chosen region.
> - Identity Platform blocking functions on custom-token sign-in.
> - `google_model_armor_template` and the Identity Platform TOTP config in the Google provider.
> - Whether `run.app` is reachable from deny-all and VPC-SC jobs.
>
> **Cost of evals:** Opus-based golden tasks and red-team runs happen **on change** (when agent files, the rubric or prompts change) **plus weekly**, not nightly. Nightly runs are limited to cheap evals.

- **M0: Harness.** `AGENTS.md` + standards pack + rubric, `.claude` hooks/agents (adapted), `check.sh`, `dev.sh`, compose, devcontainer.
- **M1: Core features working locally:**
  - Spec, backend foundation (Postgres, row-level security, auth, guard fakes, LLM replay), seed builder + gate.
  - Job domain port (CRUD, scoring, kits SSE, chat SSE).
  - Web (CRUD UI, pipeline drag-and-drop, dashboard).
  - Dev board (CRUD, drag-and-drop, state machine, triage agent, research agent with visible steps).
  - Local cron jobs.
- **M2: Single dev agent + Publisher + review**, locally: Docker sandbox, LocalGitHost, coordinator API, Claude-only review, run-tree UI.
- **M3: Cross-model review:** Gemini CLI reviewer, findings schema, dispute protocol, calibration set.
- **M4: Multi-agent:** plan review gate, leases + hotspot locks, test-first subtask, integration, lease map, overhead metric.
- **M5: Infra as code ready:** `infra/bootstrap` + `infra/envs/prod` + monitoring/SLOs + `deploy.yml` + `agent-pr.yml`, all passing `tofu test` locally.
- **M6: Bootstrap + initial commit** (when you ask): provision, deploy, seed, smoke test; switch to `GitHubHost`.
- **M5** also includes `security-review.yml` and `check.sh --security`, from the start, so the gate applies to the initial commit's own PRs.
- **M7: Hardening:** the `redteam` job + playbooks + canary records + tester reset, k6, runbook, demo script.

## Verification
- **Local:** `dev.sh` offline brings up the whole app; `check.sh` is green; Playwright e2e covers:
  - a CRUD round trip
  - dashboard KPIs equal SQL ground truth
  - drag-and-drop order persists
  - kit streams progressively, in segments of a sentence or paragraph each
  - local cron fires
  - research and triage steps are visible
- **Sandbox:**
  - A worker can't reach a non-Google host (integration test).
  - No credentials are present in the container's environment or filesystem (test).
  - A bundle with an out-of-lease path, protected path, new `skip`, `noqa`, an oversized diff or a secret is rejected by the Publisher (one test per rule).
  - Tests that already pass on the base fail the red→green check.
- **Multi-agent:**
  - Overlapping leases are refused (property-based test).
  - Spec hotspot changes run one after the other.
  - An expired lease is reclaimed.
  - A cross-area task produces one PR with parallel writers and a visible run tree.
- **Context discipline:** oversized messages are refused; `post_step` never appears in the lead's history; the overhead budget is met.
- **Cross-model alignment:**
  - Writer and reviewers receive a byte-identical standards pack from the base commit; the handoff and review hashes match, and a mismatch invalidates the review.
  - An agent file with an embedded style rule fails the `check.sh` lint.
  - An unformatted bundle is rejected before review.
  - A seeded layering violation is caught by import-linter or dependency-cruiser, not by an LLM.
  - The reviewer's input contains only the diff, the requirements, `AGENTS.md`/rubric and the CI summary (contract test on the built prompt; no writer steps or handoff text).
  - A `BLOCKER` with no citation is downgraded to `NIT`; `NIT`s never trigger rework.
  - Zero blockers auto-advance the card.
  - Lint-category findings are dropped.
  - A 3rd round is never started; a Claude↔Gemini disagreement or repeated blocker escalates to a human with `needs decision`.
  - The calibration set meets recall and false-positive thresholds for both models.
- **Access:**
  - Sign-up is impossible (UI, Identity Platform API, blocking function).
  - A third account's JWT and an unverified JWT are refused by the API.
  - The refusal text reveals nothing.
  - The tester can't trigger agents.
  - The user-count drift alert fires when a third user is added in a test project.
- **Dev scope:**
  - An infra-needing task is triaged `Out of scope` and can't move to `Ready`.
  - A bundle touching `infra/`, `.github/`, migrations, lockfiles, guards/auth or the harness is rejected by the Publisher (one test per path class).
  - `devops-engineer` and `cloud-architect` are never dispatched.
- **Exposure:**
  - Canary secret, project id and PII values never appear in the UI, SSE, error responses, the bundle, PRs or user-visible logs.
  - The repo and the built frontend pass the denylist scan.
  - `/healthz` and errors carry no internals.
- **PR security gate:**
  - A seeded PR with an unauthorised route, a leaked denylisted identifier or a vulnerable dependency fails the required check.
  - A clean PR passes.
  - Findings in the summary contain no secret values.
- **Red team:**
  - A run only uses the tester identity (asserted from API audit logs).
  - Requests stay within the app URLs and the rate cap.
  - The tester tenant matches the seed snapshot after the run.
  - Planting a deliberate IDOR in a test deployment makes the canary trip, the run stop and the owner get paged, with no canary content stored.
  - A confirmed finding produces a Backlog card and a new regression test.
- **Model routing:**
  - Digest and rejection/follow-up calls go to the pinned open model (asserted from `llm_calls.provider`).
  - A malformed open-model response falls back to Claude.
  - Lowering the eval score below the gate flips routing back to Claude and raises an alert.
- **Added after independent review:**
  - A **backup restore drill**: restore a point-in-time recovery clone, run smoke tests, delete it. Run in M7 and then quarterly.
  - An **end-to-end exfiltration attempt**: a task whose text tells the agent to send data out. It's blocked by Model Armor or VPC-SC, or the bundle is rejected.
  - An **SSE concurrency test**: 20 SSE streams on `f1-micro` stay under the connection budget.
  - A **replay fixture freshness check**: fixtures are re-recorded when prompts or models change, and replay fails if a prompt's hash isn't in the fixtures.
  - `verify` has no access to the deploy key (IAM test). `push` refuses an unsigned verdict or a SHA mismatch. A workflow on an `agent/*` branch can't get GCP credentials.
  - A sandbox can't write to a GCS bucket in another project (VPC-SC test, when an Organization exists).
  - A bundle adding `conftest.py`, a pytest plugin, `sitecustomize.py`, a `.pth` file or a Vitest setup file is rejected by `signer`. A changed frozen acceptance test (hash mismatch) is rejected.
  - `test-runner` can't call KMS (IAM test).
  - **An agent PR actually triggers `ci` and `security-review`**, and both checks appear on the PR's head SHA (an e2e test on a test repo).
  - The review round counter increments from both the in-app review and the PR gate, and the 3rd round escalates.
  - A secret split across two SSE chunks is redacted. A segment flagged later is retracted in the UI.
- **Infra (before the commit):** `tofu test` passes; Checkov confirms there's no public IP on Cloud SQL, no SA keys, and the sandbox firewall denies internet.
- **After the initial commit:** the apply succeeds; canary + rollback drill; uptime and SLO data flowing; a test alert fires; the Analytics tab shows page views, product events and Web Vitals p75 from `web_events` (and no id, IP or free text is stored); the deploy key can't push to `main`; the two users receive setup emails, and a third sign-in is refused; the core features demoed in prod; a non-maintainer is refused.
