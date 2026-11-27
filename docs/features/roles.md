# Roles and applications

A role and the user's application for it are one record (`/api/roles`). Everything is scoped to the signed-in user by Postgres row-level security (`app.user_id` per transaction).

- **List** filters by `status` and `stage` (repeatable), `track`, `min_score` and `q` (title or company, case-insensitive). Sort by `rank` (the board order), `-found_at`, `-score` or `-posted_at` (unknown values last). Pages use an opaque `cursor`; a tampered cursor returns 400.
- **Import** (`POST /roles`) is a manual or link import. Pasted ad text goes through the content guard first, and a blocked ad is refused (422 `guard-blocked`) without being stored. `score_now: true` queues scoring.
- **Update** is a partial `PATCH` that needs the `version` you read. A stale version returns 409 with `current_version`. Status and rank have their own operations (see `pipeline.md`). Changing notes writes a `note` event, and the first `followed_up_at` writes `followed_up`.
- **Delete** is a soft delete (`?version=`). The row is kept for dedupe but hidden from every read.
- **History** (`GET /roles/{id}/events`) lists status, note, kit, follow-up, scoring and rank events, newest first.

## Contacts and campaign targets
`/api/contacts` and `/api/targets` follow the same rules: versioned updates and soft deletes.
- A contact URL and a target name are unique per user, and a duplicate returns 422. Names are normalised for this, so "Acme Pty Ltd" equals "ACME".
- The `company` filter on contacts uses the same normalisation.
- Setting a contact to `sent` stamps `sent_at`. Setting a target to `approached` stamps `approached_at`.

## AI scoring and extraction
`POST /roles/{id}/score` returns 202 and scores in the background (feature `score`).
- **Extraction:** if the role has ad text but is missing its company, location, work mode or pay, extraction (feature `extract`) fills those first. It never overwrites anything.
- **The rule, checked in code:** the model proposes a breakdown. Code then:
  - clamps it (skills 50, track/pay 25, location 15, freshness 10);
  - recomputes freshness from the post time;
  - applies the keep rule: relevance is a veto, the bar is 45, and a mission organisation skips the bar.
- **Failure:** a failed scoring call marks the role `stage: error`.
- **Candidate profile:** it comes from the user's own `user_settings.profile`, experience records and opted-in rejection lessons. Nothing is hard-coded.

## Rejections and follow-ups (domain services, no HTTP route yet)
- **Rejection analysis:** `RejectionService.analyse` sorts a rejection's reasons into the fixed categories the dashboard counts (feature `rejection_analysis`).
- **Follow-up drafts:** `FollowupService.draft` suggests up to three people to follow up with after applying (feature `followup`).
  - Code checks that names appear in the ad, contacts are the user's own, and emails are in the ad.
  - Messages that sell (achievements, numbers) are flagged in `problems`.
- Both features use the open-model route with the Claude fallback (`ai/models.yaml`).
