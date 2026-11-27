# Scan (simulated scraping)

Spec tag **Scan**: `runScan` (`POST /scan`) and `getScanStatus` (`GET /scan/status`). Maintainer only (403 for the tester). `runScan` is on the `agent` rate-limit tier and returns 409 `scan-running` while another scan holds the lock.

## Pipeline
`app.domain.jobs.scan.ScanService`, for every active account:
1. `JobSource.fetch(user, since)` (`app.domain.ports.scan`) returns `ScrapedRole`s. For now the only source is the **seed feed** (`app.adapters.scrapers.seed_feed`, `app.adapters.db.scan.PgSeedFeed`): each call releases the user's next `SCAN_BATCH_SIZE` (default 5) held-back roles from `role_pool`, oldest-posted first. A released role is found now and posted 0-120 minutes ago (fixed per external id). Real Seek and LinkedIn scrapers implement the same port later.
2. Dedupe on `(source, external_id)` against the user's roles (`KnownRoles`).
3. Save the role (`status` and `stage` `new`), **then** score it through the jobs area's scorer, which sets Act now (`scored`) or `filtered` by the user's threshold. A scoring failure or the scan's daily spend cap (`JOB_CAPS[scan]`, $0.50 of `score`) leaves the role saved and unscored (`failed`).
4. Record the run in `scan_runs` (started/finished, trigger, source, fetched, new, duplicates, scored, failed, pool remaining, note) and in `job_runs` as job `scan` (Health jobs list).

An empty pool finishes `no_new_roles`, not an error; the main page then shows the info alert "No new roles".

## Seed and the hidden pool
`app.jobs.seed_pool.visible_role_ids` picks 300 visible roles deterministically: every role with history (applied/unknown/rejected, or referenced by chats, follow-ups, rejections, recruiters, experience, campaign activity or kit events), then the newest Act now roles, then the newest others. The other 1219 seed roles go to `role_pool` (RLS, user-owned). Nothing but the seed feed reads `role_pool`, so pooled roles never appear in a list or a count. Events of pooled roles aren't loaded; their LLM calls keep their spend without the role link. `dev.sh --reset-db` reloads the same split.

- **Owner only.** The demo data (visible roles, pool, records, contacts, spend) belongs to the owner (maintainer). The tester account is provisioned with an empty tenant, so a fresh database has 300 roles and 1219 pooled, not two copies.
- **Released once.** A release marks its rows consumed (`released_at`) in the same `UPDATE … RETURNING`, so two scans can't release a row twice. Consumed rows stay in the table so the idempotent seed (run on every `dev.sh` start) can't put them back; `pool_remaining` counts only rows still waiting (`released_at IS NULL`) for active accounts.
- **Not reliant on RLS alone.** The release, the pool count and the dedupe lookup all filter on `user_id` explicitly as well, so a job run with a privileged URL (which bypasses RLS) still sees one account at a time. Regression: `tests/integration/jobs/test_seed_pool_once.py`.

## Schedules
- Local: with `SCAN_INTERVAL_MINUTES` set, the API process runs the scan on that interval in a background task started and stopped in the lifespan. Single flight with manual scans via a Postgres advisory lock (`PgScanLock`); a tick that finds a scan running is skipped.
- Prod: Cloud Scheduler runs `python -m app.jobs.scan` every 15 minutes (`schedules.feed_replay` in `infra/envs/prod/variables.tf`, key kept for `main.tf`). `python -m app.jobs.feed_replay` is an alias.
