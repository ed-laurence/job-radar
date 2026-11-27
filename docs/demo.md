# Walkthrough

A tour of Job Radar in about 15 minutes. It follows a role from arriving in the feed to an application, then covers the dev board and what happens behind each screen.

## Before you start
1. Live Claude (recommended): `~/.config/jobradar/local.env` contains
   `LLM=vertex`, `CLAUDE_BACKEND=anthropic`, `ANTHROPIC_API_KEY=…`.
   - Once Vertex quota is approved, use `CLAUDE_BACKEND=vertex` and `GOOGLE_CLOUD_PROJECT=<project>` instead.
   - To run offline, set `LLM=replay`: recorded answers, plus synthetic ones labelled `[synthetic … output]`.
2. Run `bash scripts/dev.sh`. Wait for `api ->` and `web ->`, then open http://127.0.0.1:3000.
3. Sign in with the **owner** account (set up with `scripts/setup-identity.sh`). Only the two provisioned accounts can sign in; there's no sign-up. Without Identity Platform, `AUTH=fake` shows a local account picker.
4. For a clean start, use `bash scripts/dev.sh --reset-db`: 300 synthetic roles, with more arriving on each scan.

## 1. Set up your profile
Scoring, the evidence test and kits all read your profile, so start in **Settings**:
- **CVs:** technical, leadership and your LinkedIn profile text.
- **Extra skills** and **experience records**: things you've done that aren't on your CV. Each record is a requirement plus the contexts where you showed it.
- **LinkedIn connections** import, used to suggest people to contact.
- **Scoring:** weights, pay floors and the keep threshold that decides which roles reach "Act now".

Your profile is de-identified before it goes into any prompt.

## 2. Roles arrive
- The top bar shows the scan status ("Last scan … · next in …"). **Scan now** pulls new roles from the feed; the schedule does the same every 15 minutes. In this build the feed is simulated from seed data.
- Each new role is extracted (title, company, pay, location, must-haves) and scored against your profile, then lands on the **Dashboard**.
- To add one by hand: **Roles → Import role**, paste the ad or a link, and save. A pasted ad passes the content guard first; an ad carrying an injection attempt is refused and not stored.

## 3. Decide what to act on (Dashboard)
- **Headline tiles:** To act on, Oldest waiting, Applied in the last 7 days, Median time from posting to applying.
- **Status tabs:** Act now, Applied, Interviewing, Passed, Rejected, Unknown and Filtered.
- **Role cards:** a colour-banded score, how long ago it was posted, the evidence line ("Apply · you show 4 of 5 must-haves"), and actions: Open listing, Write kit, Mark applied / Pass, Ask.
- **Evidence test:** expand the evidence line to see each must-have with the quote from your CV or records that proves it. Code checks every quote against your actual text, so a quote the model invented is dropped and the item drops to "partial". Click **I've done this** on a ≈ or ✗ item to add an experience record; the test runs again.

## 4. Prepare an application
Open a role:
- **Write kit** streams a tailored kit section by section: positioning, CV changes, cover note and outreach. (The API also answers application-form questions; the screen doesn't ask for them yet.) Anything that looks like a secret or personal data is replaced with `[redacted]` as it streams.
- **Chat** streams answers about this one role, grounded in the ad, the assessment and your kit.
- **Research** starts the company research agent (see step 6).
- Edit the role, or change its status. Open it in two tabs and save both: the second save shows the newer version and lets you retry rather than overwriting it.

## 5. Track it through to an outcome
- Change the status as things move: applied, interviewing, offer, rejected. Every change is logged and feeds the analytics.
- Marking a role **rejected** asks for the reasons (skills gap, seniority, pay, role filled…). The dashboard counts them, so patterns show up over time.
- The API can also draft follow-up messages to contacts at the company and analyse a pasted rejection message (`docs/features/followups.md`, `docs/features/rejections.md`); neither has a screen yet.
- **Job analytics** (expand it under the tiles): response, interview, offer and ghost rates, the funnel, posting-to-apply time, score distribution, rejection reasons, applications per week, and outcomes by source, track and score band. Every chart has a text summary and a data table.

## 6. Research employers and keep in touch
- **Employers** are your target companies, grouped by lane (*Data platform & architecture*, *AI & LLM engineering*, *Data & AI leadership*).
- **Start research** on an employer: the agent works one turn at a time, calling tools (`lookup_known_data`, and web search when `RESEARCH_WEB_SEARCH` is on) until it gives a summary, findings and sources. Each turn streams as a step, so you watch it think.
- **Contacts** holds the people you're talking to, with their company and where things stand.
- **The job-search plan** (left column of the dashboard on wide screens): this week's targets against actuals, the week's tasks (‹ › moves between weeks) and **Log activity**.

## 7. Keep an eye on cost and health
**Health** in the top bar (maintainer only) shows:
- the last and next run of each scheduled job;
- LLM spend today and this month, calls, fallback rate, and spend by feature and provider;
- site usage: page views, sessions and Core Web Vitals p75 against budget.

Scheduled jobs: the scan, a nightly digest, metric rollups and analytics purge, and a watchdog. Each has its own daily spend cap. Run one by hand with `cd backend && uv run python -m app.jobs.digest`.

## 8. The dev board
A Kanban board for developing Job Radar itself. Cards carry AI agents.
- **New task:** triage runs on its own. Open the card to watch its steps stream, then see the type, size and risk (code decides risk; the model can only raise it), executable acceptance criteria, and whether the task is in scope (web and API work only).
- **Approve plan + tests** (maintainer) starts the plan run, which also streams its steps. The coding agent after that is simulated in this build: the steps are realistic, but no code is written.
- **Drag cards** between columns, or use the keyboard: Tab to a card's handle, **Space**, the **arrow keys**, **Space** again. A screen reader announces each step. A server-side state machine decides every move: drop a card on *Done* and the move is refused, the reason shown, and the card snaps back.
- **Board metrics:** throughput, WIP by column, time per column, lead time, agent pass rate, review rounds, cost per task, and the oldest card waiting for human review.

## How it works
- **One way in.** The browser only talks to the Next.js app. It proxies `/api/*` to the FastAPI backend, so the backend's address is never exposed.
- **Your data is yours.** Every table is scoped to the signed-in user by Postgres row-level security, and the API refuses to start if its database role could bypass it. Edits carry a version number, so two tabs can't silently overwrite each other.
- **AI calls go through one service.** It picks the model per feature (`ai/models.yaml`), falls back when a model fails, records cost, and stops a feature when its budget runs out. Untrusted text (ads, uploads, task descriptions) is passed to the model as data, never as instructions, and is screened by the content guard on the way in and out.
- **Code checks the model.** Scores, verdicts, risk levels and quotes are validated or decided in code, not taken on trust.
- **Streaming.** Kits, chat and agent steps arrive as Server-Sent Events. If a later segment fails a check it's retracted; an error mid-stream never leaks details.
- **Offline by default.** With `LLM=replay` and fake guards, everything runs without network access or cost.

Design detail: `docs/architecture.md`. Per-feature behaviour: `docs/features/`.

## Known limits
- Runs locally; the cloud deploy is next.
- The sandboxed coding and review agents on the dev board are simulated.
- Open-model routes fall back to Claude until Vertex is configured.
