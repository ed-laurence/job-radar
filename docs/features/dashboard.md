# Dashboard

All endpoints live under `/api/dashboard/` and share three query parameters:
- `from`: inclusive start date. Default: 90 days before `to`.
- `to`: exclusive end date. Default: tomorrow. Dates are UTC.
- `bucket`: `day`, `week` (starting Monday) or `month`.

The range can be at most three years. An inverted or oversized range returns 422.

| Endpoint | What it counts |
|---|---|
| `kpis` | Open roles (`new`, `reviewing`, `applied`, `interview`, `offer`). Roles applied in the range. Interview rate (the share of those that reached `interview` or `offer`). Median posted-to-applied hours. LLM spend. Agent pass rate. Cards in flight (not `backlog`, `out_of_scope` or `done`) |
| `funnel` | Roles that reached each status in the range, from status events. `new` also counts roles found in the range |
| `post-to-apply` | Hours from `posted_at` to `applied_at` per bucket, plus the overall median and p75 (linear interpolation, like `percentile_cont`) |
| `score-distribution` | Scored roles found in the range, in ten bins (0-9 … 90-100) |
| `rejection-reasons` | Rejections in the range, by fixed category |
| `llm-spend` | The caller's LLM calls, cost and tokens per bucket, feature and provider |
| `dev-cycle-time` | Hours from entering each board column to the next move, for moves that end in the range |
| `job-insights` | `from`/`to` only (no bucket). Over roles applied in the range: applications per ISO week (Monday, latest 60); response rate (reached `interview`, `offer` or `rejected`, from status events or the status now), interview rate (`interview`/`offer`), offer rate, ghost rate (status now `unknown`); median days from `applied_at` to the first later reply event; follow-up on-time rate (of roles followed up or past due, the share followed up within the caller's `followups.due_hours`). Breakdowns by source, track (`unknown` when unset) and score band over roles found in the range: found, applied, interviews, rejected. Bands split at the caller's borderline, keep and notify scores (default `0-44`, `45-54`, `55-74`, `75-100`, plus `unscored`). Rates are null with nothing to divide |
| `agent-pass-rate` | First-round (`round = 0`) review runs finished in the range, split by plan mode (`single`/`multi`). A run passes when its outcome is `approved` (zero blockers) |

Every number is computed in `app.domain.jobs.dashboard` (insights in `app.domain.jobs.insights`) from raw facts. `tests/integration/jobs/test_dashboard_ground_truth.py` and `test_insights_ground_truth.py` check each one against an independent SQL query over the loaded seed. The seed has no board data, so the board metrics tests insert their own cards, runs and triage (`tests/integration/jobs/board_data.py`).

## Seed loader (`python -m app.jobs.seed`)
- **Who runs it:** run it with the database owner's `DATABASE_URL`, because it provisions `users`.
- **Accounts:**
  - `owner@example.test` maps to the `APP_USERS` maintainer and `tester@example.test` to the `user` account.
  - A seed account with no matching role in `APP_USERS` is skipped.
  - With no `APP_USERS` at all, the seed accounts themselves are created.
- **Idempotent:** every row gets a deterministic id and inserts skip existing rows, so a re-run writes nothing.
- **Dates:** shifted by `now - anchor`.
- **Scope:**
  - Job-search rows load per account, under that account's RLS scope.
  - The seed has no dev-board data: no cards, runs or steps load, so the board starts empty.
- **Mapping:**
  - Vocabulary maps to Job Radar's values: legacy sources become `seed`, and job-seeker rejection categories and LLM features map to the spec enums.
