# Evidence test

After job-seeker's evidence test: does the user's own material already prove what the ad needs?

## Operations (spec tag Scoring)
- `GET /roles/{id}/evidence` (`getRoleEvidence`, default tier): the latest test. 404 for another user's role or one never checked.
- `POST /roles/{id}/evidence` (`runRoleEvidence`, `ai` tier): "Run again". Runs the test, stores it (replacing the last one) and returns it as plain JSON. 422 when the role has no ad text (refused before any model call).
- **"I've done this"** needs no endpoint of its own: the web opens the experience-record form prefilled with the requirement, saves it with `createExperience`, then calls `runRoleEvidence`.

## How it runs
1. One structured LLM call, feature `evidence` (`ai/models.yaml`: Claude Sonnet, Opus fallback). Data blocks: `candidate_evidence` (CV texts by slot, extra skills, experience records with ids), `listing`, `job_ad` (untrusted). The adapter applies the content guard and PII de-identification on the way in and out.
2. The model lists the ad's top must-haves and, for each, `match` (shown/partial/missing), a quote, its `source` (`cv_technical`, `cv_leadership`, `records`, `none`), `record_id` and a one-line `why`; plus the ad's team size and the largest team the user has led.
3. **Code checks every quote** (`app.domain.jobs.evidence_test.check_item`): it must appear in the claimed text, ignoring whitespace runs and case. A quote found in the other CV or another record is re-attributed; one found nowhere is dropped and `shown` becomes `partial` ("No exact line on your CV or records backs this up."). `missing` never carries a quote. Quotes are capped at 600 characters, `why` at 200.
4. **Team check:** `ok` within twice the largest team led, `over` beyond it, `not_stated` when the ad or the user's material doesn't say.
5. **Verdict (code):** `apply` when every must-have or all but one (and at least one) is shown, the team isn't `over`, and the score is at least the user's keep threshold (`scoring.keep_score`); `consider` when it passes on evidence or on score; otherwise `skip`. The verdict is recomputed on read from the role's current score. `verdict_text` reads "Apply · you show 4 of 5 must-haves (1 from your records, not on your CV)".

## Storage and the card summary
- `role_evidence` (migration 0005, user-owned, RLS): one row per role, the result as JSON plus `model` and `checked_at`.
- `Role.evidence` stays a summary: a run writes `score_breakdown.evidence_test` (counts, team-over flag, keep threshold, items with CV slots folded to `cv`), which wins over the scoring estimate. Rescoring keeps it.

## Offline
Replay fixtures live in `backend/tests/fixtures/llm/jobs/evidence/`. With `LLM_REPLAY_MISS=synthesize`, synthetic answers validate but their quotes match nothing, so they show as partial: safe for offline dev.
