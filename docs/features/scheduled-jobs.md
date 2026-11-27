# Scheduled jobs

Cloud Scheduler → Cloud Run Jobs in prod; locally run them by hand. Each prints one JSON line (`job`, `ok`, `items_seen`, `items_new`, `error_code`, `notes`) and exits 1 when not ok. Each writes a `job_runs` row (Postgres) and has its own daily spend cap on top of the per-feature budgets in `ai/models.yaml`.

| job | command | schedule | cap |
|---|---|---|---|
| feed replay | `uv run python -m app.jobs.feed_replay` | hourly | $0.50/day of `score` spend |
| digest | `uv run python -m app.jobs.digest` | nightly | $0.20/day of `digest` spend |
| rollup | `uv run python -m app.jobs.rollup` | nightly | no AI spend |
| watchdog | `uv run python -m app.jobs.watchdog` | every 5 min | no AI spend |

## Feed replay
Releases up to 20 held-back seed roles whose `release_at` has passed (oldest first, per user under that user's RLS scope): `release_at` is cleared, `found_at` set to now and `status` to `new`. If the container carries a scoring service (`role_scorer` with `score_role(user_id, role_id)`), each released role is scored until the cap or the model budget runs out; otherwise the roles are released unscored and the report says so.

## Digest
Counts the last 24 h of board activity (events by kind, finished runs by status, cards per column) and asks the `digest` route, an open model on Vertex Model Garden (MaaS) with automatic fallback to Claude Sonnet, for a headline and up to five bullets. The result is stored in `app_state` under `digest:latest`. With the cap used up, or offline without a recording for the day's counts, a plain template digest is stored instead (`provider: template`).

## Rollup
Creates this and next month's `web_events` partitions, purges `web_events` older than 90 days, and notes the board's cards per column.

## Watchdog
Marks expired, unreleased path leases released, then checks that Identity Platform holds exactly the provisioned accounts (the `APP_USERS` count; sign-up is off, so an extra account fails the job with `unexpected_user_count`). With `AUTH=fake` the directory is a fake; with `AUTH=identity_platform` the check is skipped until the directory adapter exists.

Offline (`LLM=replay`, no `DATABASE_URL`) every job runs against in-memory stores and the board's replay fixtures.
