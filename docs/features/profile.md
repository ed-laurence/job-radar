# Profile: CVs, experience records and LinkedIn connections

The Settings sections that make up the candidate profile scoring and kits use: CV texts + extra
skills (`settings.profile.skills`) + experience records, de-identified. With none set, every
prompt is byte-identical to before (replay fixtures still match).

## Documents (`/profile/documents`)
- Slots: `technical`, `leadership` (CVs) and `linkedin_profile` (LinkedIn's profile PDF).
- `PUT /profile/documents/{slot}` takes `{filename, data: base64}`. Refused: not starting with
  `%PDF-` (422), over 5 MB decoded (413), over 20 pages, encrypted or unreadable (422), text the
  content guard blocks (422 `guard-blocked`). The file name is sanitised (no path or shell
  characters, at most 120 chars, `.pdf`).
- Text is extracted with pypdf in a worker thread (15 s limit, 200k chars, two at a time),
  guarded (`ContentGuard.inspect_input`) and de-identified (PII port) before it is stored for
  prompts; content is never logged. Each document contributes at most 20k chars to a prompt
  (`cv_technical`, `cv_leadership`, `linkedin_profile` in the candidate profile block).
- The readability check counts section headings, role lines with a date range and bullets;
  `ok` needs two sections and a role line or three bullets.
- Storage: through the `Storage` port under `users/<user id>/profile/<slot>/<random id>.pdf`
  (plus the `.txt` text; the per-user manifest lives in the `profile_documents` table under RLS since migration 0004, with a one-time read of any older `manifest.json`). Keys come from the signed-in user and
  a server id, never the client. A replaced file is archived (latest five kept); `DELETE`
  removes the current file and its text. Local disk (`.artifacts/storage`) with Postgres, memory
  offline.
- `GET .../{slot}/file` streams the PDF inline (`no-store`, `nosniff`).

## Experience not on your CV (`/experience`)
- A record (after job-seeker) is the `requirement` it answers (1 to 200 characters) plus 1 to 10
  `entries`, one per role or project: `{context, detail}` (where, 1 to 200; what you did there,
  1 to 1500). Whitespace is collapsed and blank rows are dropped; a half-filled row, a blank
  requirement or no rows left is a 422 with field errors (`/entries/<i>/detail`...). An
  optional `role_id` must be one of the caller's roles. Versioned (409 when stale).
- Responses add `ref` (`E<n>`, the position oldest first), entry refs (`E<n>.<k>`) and `uses`
  (how many roles' stored evidence tests show a must-have from the record).
- `GET /experience` also returns `contexts`: the roles on the caller's uploaded CVs ("Title,
  Employer", technical CV first, then leadership; duplicates and "Earlier career" skipped),
  then "Personal project" and "Other". Parsed from the stored CV text's EXPERIENCE section
  (`domain/jobs/cv_roles.py`).
- Stored in `experience` (requirement, role) and `experience_entries` (context, detail), in
  order. In prompts a record reads as its requirement with one "context: detail" line per
  entry; the evidence check cites it by id as `source: records`.

## LinkedIn connections
- `POST /contacts/import` with `Connections.csv` (base64, at most 2 MB and 10,000 rows). The
  notes preamble is skipped (the header row is found by First Name / Last Name); columns map to
  first/last name, URL, email, company, position, connected on. Rows need a name and an http(s)
  URL (else `skipped`); upsert is by profile URL (`imported` new, `updated` existing, reviving a
  removed one; status, message and notes kept). Cells starting with `= + - @`, tab or CR get a
  leading apostrophe.
- `DELETE /contacts` with `{confirm: true}` removes every contact the caller owns (RLS).
- `GET /profile/linkedin-import`: the current contact count and the last import's summary.

## Roles age filter
`GET /roles?min_age_days=&max_age_days=` (0 to 365). Age runs from `posted_at`, else
`found_at`. `max_age_days=7` keeps roles seen in the last 7 days inclusive; `min_age_days=14`
keeps roles strictly older than 14 days; min above max is 422.

Code: `app.domain.jobs.{documents,experience,linkedin}`, `app.api.jobs.profile`,
`app.adapters.{pdf,storage_local}`, `app.adapters.db.profile_stores`,
`app.adapters.fakes.profile`. Tests: `tests/unit/profile/`,
`tests/contract/jobs/test_profile_contract.py`,
`tests/integration/jobs/test_profile_repositories.py`.

## Storage backends
`STORAGE=local` (default; `.artifacts/storage`) or `STORAGE=gcs` with `STORAGE_BUCKET` (`app.adapters.storage_gcs.GcsStorage`: the GCS JSON API over httpx with Application Default Credentials, so it works over Private Google Access). The prod settings validator requires `gcs` and a bucket.
