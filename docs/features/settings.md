# Settings

`GET /api/settings` returns the caller's settings; `PUT /api/settings` replaces them. They live in
`user_settings.settings` (one `jsonb` document per user, under RLS; no migration). Job scraping
settings (searches, scan cadence, active hours, scraper budgets) are deliberately not exposed.

## Rules
- **Defaults:** a user with no row, or a document missing a key, reads the defaults: keep 55,
  borderline 45, notify 75, notify mission 65; follow-ups after 24 h, due 48 h, nudge 7 days;
  auto kits off, 5 kits per run, spelling `en-AU`; archive 14 days; time zone
  `Australia/Melbourne`; campaign start `null`; profile skills and brief empty.
- **Versions:** an unset user reads version 1. A `PUT` with the version read saves and bumps
  it; a stale version is 409 with `current_version`.
- **Validation (422):** borderline above keep, `due_hours` before `after_hours`, or an unknown
  time zone (on hosts without a zone database only the IANA name's shape is checked).
- **LLM budget:** `llm.monthly_budget_aud` (AUD, 0 to 10000) is applied only when the caller
  is a maintainer; a non-maintainer's value is ignored and the stored value kept. Never set,
  it reads the deployment's `LLM_BUDGET_MONTH_AUD`; `null` set by a maintainer means no
  monthly cap. Providers bill in USD (`llm_calls.cost_usd`), so spend is converted at
  `USD_TO_AUD` (default 1.55, must be > 0) before any comparison or display. A document saved
  before the switch holds `monthly_budget_usd`; it is read as AUD and rewritten under the new
  key on the next save.
- **Scoring preferences:** `scoring_preferences.weights` (skills, track_pay, location,
  freshness) must add up to 100 (422 otherwise; default 50/25/15/10). Pay floors are nullable,
  `work_modes` is a subset of remote/hybrid/on_site, `preferred_locations` holds at most 10. A
  `PUT` that omits `scoring_preferences` keeps the stored ones.
- **Kept keys:** a `PUT` keeps document keys the API doesn't own (the seeded profile's `name`,
  `headline`, `location`).

## Where each setting applies
| Setting | Effect |
|---|---|
| `scoring.keep_score`, `borderline_score` | Scoring filters a relevant, non-mission role below the lower of the two (stage `filtered`, off Act now). Insights score bands split at them |
| `scoring.notify_score` | Insights score bands. (No notifications exist yet; `notify_mission_score` is stored only) |
| `followups.after_hours` | `followups_due` on the job summary and the follow-up alert |
| `followups.due_hours` | Insights follow-up on-time rate |
| `archive_default_days` | `RoleService.archive_older` when no `days` is given (the API still requires `days`; the web pre-fills it) |
| `profile.skills`, `profile.brief` | "Your extra skills" (treated as fact alongside your CVs) and the brief, added to the candidate profile block of scoring, kit and chat prompts as `skills_and_experience` and `looking_for`. Empty values leave prompts byte-identical, so replay fixtures still match |
| `scoring_preferences.weights` | The caps in the scoring instructions and the breakdown clamp; freshness points scale to its weight. The defaults reproduce today's instructions exactly (same prompt hash) |
| `scoring_preferences` pay floors, work modes, places | When any is set, a `scoring_preferences` data block and one fixed instruction paragraph join the scoring prompt; a role whose work mode isn't accepted scores 0 location points and gets a watch-out |
| `kits.spelling` | The profile `spelling` in prompts and the kit spelling check (`en-US` is not checked) |
| `campaign.start_date` | `/campaign` week 1 starts on that date's Monday instead of the plan's start |
| `llm.monthly_budget_aud` | The LLM spend cap (the user's calls this UTC month, converted to AUD; `BudgetExceeded` once reached) and the health panel's `budget_month_aud` |
| `kits.auto_kits`, `kits_per_run`, `followups.nudge_days`, `display.timezone` | Stored for the web and later jobs |

CVs, experience records and LinkedIn connections: see [profile.md](profile.md).

Code: `app.domain.user_settings`, `app.api.user_settings`, `app.adapters.db.user_settings`,
`app.compose.user_settings`. Tests: `tests/unit/settings/`,
`tests/integration/jobs/test_insights_ground_truth.py`.
