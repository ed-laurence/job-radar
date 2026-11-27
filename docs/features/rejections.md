# Rejection analysis and patterns

## Analysing a rejection
`POST /roles/{id}/rejection-analysis` reads the rejection recorded for the role (moving a role to `rejected` creates one) and sorts the reasons into the fixed categories the dashboard counts.
- It needs the rejection message or the feedback pasted in first (422 otherwise). A role with no rejection is 404.
- The message and feedback go to the model as delimited data blocks and pass the input guard (feature `rejection_analysis`, on the open-model route with the Claude fallback).
- The answer is validated. Each reason gets a category (`skills_gap`, `experience_level`, `seniority`, `location`, `pay`, `visa`, `internal_candidate`, `role_filled`, `stronger_candidate`, `culture_fit`, `no_reason_given`, `other`) and a short detail. A message with no reason gives `no_reason_given`.
- The response has the categories, a one-sentence summary, a lesson for future applications, and whether lessons feed into kits (`use_in_kits`). Running it again replaces the earlier analysis.

## Patterns
`GET /rejections/patterns` summarises the caller's rejections on live roles (the latest 500):
- `total` rejections, and how many were `analysed`.
- `categories`: counts per category, most common first.
- `lessons`: the latest five distinct lessons.

Display labels for the categories come from `x-enum-labels` in the spec.
