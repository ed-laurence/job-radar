# Campaign plan and main-page extras

The main page's left column: a weekly plan with targets, a task checklist and an activity log.
Ported from job-seeker's campaign; the plan in the repo is generic and synthetic.

## The plan

- `config/campaign.yaml` is the default plan. Set `CAMPAIGN_CONFIG` to a file outside the repo
  to use a private one. The file is re-read when it changes.
- Fields: `name`, `weeks` (1-52), `start` (a date) or `start_offset_days`, `targets` (weekly
  target per metric key), `week_targets` (per-week overrides), `checkpoints` (`week`, `text`),
  and `tasks` (`id`, `title`, `weeks`, `when`, `steps` in markdown, `links`, optional `metric`).
- `tasks[].weeks` is `all`, a list of week numbers, a number, or a range like `"2-7"`.
- Without an explicit `start`, the plan starts `start_offset_days` from an anchor: the seeded
  demo's anchor date (stored by the seed job as the `seed_anchor` campaign note), else today.
  The start always snaps back to a Monday.

## Week maths

Week 1 is the seven days from the start. A date's week is `(date - start).days // 7 + 1` (UTC
dates). `current_week` can be below 1 or above `total_weeks`; the selected `week` defaults to
the current week, clamped to the plan.

## Actuals

Metric keys: `agency_calls`, `submissions`, `network_messages`, `invites`, `conversations`,
`direct_approaches`, `applications`, `followups`, `screens`. Logged activity counts towards the
metric of its kind (for example `agency_call` to `agency_calls`, `linkedin_invite` to
`invites`). Applications count roles by `applied_at`; follow-ups count `followed_up` role
events. Tasks with a `metric` show progress against that week's target.

## Main-page extras

- `POST /roles/archive-older` moves Act now roles (new or reviewing, not filtered) posted, or
  found when the post date is unknown, more than `days` days ago to `closed`. Each is a versioned
  write with a status event.
- `GET /alerts`: failed scheduled jobs (error), applications to follow up (warning), and a feed
  that ran but found no roles for two days (info). Texts never include internals.
- `JobSummary.followups_due`: applied, not followed up, applied at least a day ago.
  `tab_counts.outreach`: contacts with status new or drafted (`GET /contacts?needs_outreach=true`
  lists them). `tab_counts.feedback`: rejections without analysed reasons
  (`GET /rejections` lists rejections with `reason_status` missing, pasted or analysed).
- Role `evidence`: from scoring's optional must-have list (`items`: requirement, match, source),
  stored in `score_breakdown.evidence` so no migration is needed. Roles without it get an
  estimate from the skills subscore over five must-haves (no items); roles without a breakdown
  have none. Verdict: apply, consider or skip.
