# Job Radar

An AI-assisted job-search workspace on Google Cloud, plus a dev board whose cards run AI agents against this repo.

## Job search
- **Roles and applications.** Import a role by pasting the ad or a link, or let the scan find new ones. Each role and its application share one record, private to you.
- **Scoring and evidence.** AI scores each role against your profile (CVs, skills, experience records) and runs an evidence test: does your own material already prove what the ad asks for?
- **Pipeline.** Drag-and-drop Kanban of your applications by status, keyboard accessible, with ordering kept on the server.
- **Application kits.** Streamed CV tailoring, cover note, outreach message and answers to application-form questions.
- **Role chat.** A streamed conversation about one role, grounded in the ad, the assessment and your kit.
- **Follow-ups and rejections.** Drafts follow-up messages to real contacts after you apply; sorts rejection feedback into categories and shows the patterns.
- **Company research agent.** A tool-using agent researches a target employer (overview, hiring signals, people to approach, news) and streams each step.
- **Contacts and employers.** A small CRM for the people and companies you're working with.
- **Dashboard.** A weekly campaign plan with targets, tasks and an activity log; headline tiles; job analytics charts; and a Health panel for scan status, LLM spend and site usage.
- **Scheduled jobs.** Feed replay, nightly digest, metric rollups and a watchdog, each with its own spend cap.

## Dev board
A Kanban board for developing this app. A server-side state machine moves each card through triage, plan review, agent development, agent review and human review. AI triage and planning run today; sandboxed coding agents with cross-model review come later.

## Safety
- Postgres row-level security scopes every query to the signed-in user.
- Scraped ads, uploads and agent input pass through a content guard (Model Armor) and PII redaction (Sensitive Data Protection) before reaching a model.
- Sign-up is off; accounts are provisioned. See `docs/security.md` for the threat model.

## Quick start (local)
Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20+ with corepack (`corepack pnpm`), and Git Bash or WSL on Windows. Docker is optional: without it the scripts use an embedded Postgres.

```bash
bash scripts/dev.sh          # web http://127.0.0.1:3000, api http://127.0.0.1:8000
bash scripts/check.sh        # every quality gate (PASS / FAIL / SKIP with reason)
cd web && corepack pnpm e2e  # Playwright journeys; start the app offline first: LLM=replay bash scripts/dev.sh
```
- LLM answers are live by default, locally too (Claude via Vertex, or the Anthropic API while Vertex quota is pending). `LLM=replay` opts into recorded answers offline; the automated tests always replay.
- For live Claude, put private overrides in `~/.config/jobradar/local.env`, **outside the repo**. Start from `env.example`, which lists every variable. See `docs/demo.md`.

## Stack
Python 3.12, FastAPI, SQLAlchemy 2, Alembic and Postgres 16 (`backend/`); Next.js 16 with strict TypeScript (`web/`); a spec-first OpenAPI contract (`api/openapi.yaml`); OpenTofu for GCP (`infra/`): Cloud Run, Cloud SQL, Vertex AI, Model Armor and Sensitive Data Protection.

## Where to look
- `docs/demo.md`: a 15-minute walkthrough.
- `docs/features/`: how each feature behaves.
- `docs/architecture.md`: the system as built. `docs/plan.md`: design and milestones. `docs/adr/`: architecture decisions.
- `AGENTS.md` and `docs/standards/`: the rules every human and agent follows.

## Status
- All job-search features and the dev board's triage and planning work locally, and every e2e journey passes.
- **Next:** GCP deploy, then sandboxed coding agents with cross-model review, then hardening.
