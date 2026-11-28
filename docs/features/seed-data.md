# Seed data

`seed/` holds the synthetic data the app loads locally (`dev.sh`) into the **owner's** tenant only (the tester starts empty): 300 visible roles plus a hidden pool of the rest, released 5 per scan. It looks like a real job search. It contains no real people, ad text, prompts or owner details.

## Build

```
uv run scripts/build_seed.py --source <job-seeker>/data/jobradar.db --out seed/
uv run scripts/build_seed.py --verify            # the PII gate, run by check.sh
```

- The source database is opened read-only (`?mode=ro`). Only the tables listed below are read.
- The output is deterministic: the same input and flags give byte-identical files.
- `seed/manifest.json` records the schema version, the `anchor`, and a count and sha256 for each file.
- All dates are shifted so the newest source record lands on `anchor` (default `2026-11-25T00:00:00Z`), keeping relative spacing. Loaders should re-base on `now - anchor` at load time.
- `--salt` (or a changed default) changes every synthetic id and fake company name.
- `--keep-companies` keeps real employer names. **Local use only:** it writes to `seed/local/`, which is gitignored (`seed/.gitignore`) and skipped by `--verify`.

## What's in it

| File | Source | Treatment |
|---|---|---|
| `roles` | roles, ~1,500 sampled | Stratified by source × status × score band. Every non-`new` status is kept (capped at 200 per stratum), and `new` fills the rest proportionally. Title words are kept only if they appear in titles from 5+ employers, which drops employer names, people and small suburbs. Kept fields: location, salary band (parsed and rounded), work mode, score, breakdown, stage, track, status and dates. The description is a full ad built from one of the hand-written role-family templates in `scripts/seed_ad_templates.json` (the first whose `match` pattern hits the title; non-tech titles get a generic one). Each ad has an intro, "What you'll do", "What you'll need" (five must-haves, with years set by seniority), "Nice to have", up to 8 skills from a fixed allowlisted vocabulary, and the package. Scored roles also get `score_breakdown.evidence`: those five must-haves, with as many `shown` as the skills subscore implies, one `partial`, and the rest `missing`. The role panel lists them before the full evidence test has run. `--refresh-ads` rewrites only these two fields in an existing `roles.jsonl`, so the rest of the seed doesn't change. Synthetic `external_id`, `https://jobs.example.test/...` URLs, and hashed fake company names. |
| `role_events` | role_events | Timing and kind kept, re-keyed to new role ids. `from`/`to` kept for `status` events only. |
| `llm_calls` | llm_calls | Feature, model, tokens, timestamp and ok. `provider = vertex-claude`. `cost_usd` is estimated from a fixed rate table in the script, not billing data. |
| `targets` | targets | Fake name, with lane, status and dates kept. The source has no sector or size, so those are assigned by hash. |
| `contacts`, `chats`, `rejections`, `followups`, `role_recruiters`, `campaign_activity`, `campaign_tasks_done`, `experience`, `experience_entries` | counts only | Fully synthetic: seeded Faker names, `example.test` emails, `people.example.test` URLs and templated text. Row counts and category mixes match the source. No text is copied. |
| `experience`, `experience_entries` (shape) | | Each record is a requirement plus one entry per role (`context` "Title, Employer", `detail` what was done there). Every seeded `context` is a role on the demo CVs, so it matches the contexts Settings offers. |
| `persona` | none | The fictional job seeker, Alex Example (`alex@example.test`). |
| `owner-settings.json` | none | Hand-written "Your extra skills" for Alex Example, one "Skill: what/where (year)" line each, about the CV employers but adding what the CVs don't say. The dev seed sets them on the owner's settings only while empty. Scanned by the PII gate like every `seed/**/*.json`. |
| `documents/{technical,leadership}-cv.pdf` | none | Alex Example's two CVs, rendered from the `.json` beside them by `scripts/build_demo_cvs.py`. The dev seed uploads them as the owner's technical and leadership profile documents through the normal upload path (guard, PII de-identification, storage, manifest), skipping a slot that already holds a document. |
| `users` | none | `owner@example.test` (maintainer) and `tester@example.test` (user). Real accounts come from `APP_USERS` at runtime. |
| Dev board | none | No seed data: the board starts empty, and its tasks and runs come only from real use. |

## The gate (`--verify`)

It scans every `seed/**/*.json(l)` string value except those under `seed/local/`, and fails on any of these:
- an email outside `example.test`
- a phone-like number (8+ digits)
- a `linkedin.com` or `seek.com.au` URL
- any line of `~/.config/jobradar/denylist.txt`
- in free-text fields, a Faker first name followed by a Faker surname (only "Alex Example" is allowed)

Structured name fields (`first_name`, `last_name`, `name`, `target_name`, `who`, ...) hold Faker names by design, so the name check skips them.
