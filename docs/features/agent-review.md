# Agent review, Publisher checks and human review (M2)

The rest of the dev-card flow after a dev run: `agent_dev → agent_review → human_review → done`,
with `changes_requested → agent_dev` for rework. Everything here runs locally with
`dev.sh --with-agents` (LocalGitHost); `GitHubHost` replaces the local code host later.

## 1. Publisher checks (trusted code, outside the sandbox)
After a successful dev run, the runner publishes the result bundle to the scratch remote as
`agent/<task8>-<run8>`, then reads the branch's changes (`CodeHost.changes(base, branch)`:
per file the status A/M/D/R and added/deleted line counts, plus the added lines). Pure domain
rules (`domain/devboard/publisher.py`) check them. Any violation fails the run, deletes the
branch, and blocks the card with the list of violations.

| Rule id | Check |
|---|---|
| `PUB-LEASE` | Every changed path is inside the run's leases (the plan's subtask lease paths) and `is_leasable` |
| `PUB-SCOPE` | Every changed path is writable (`is_writable`), or regenerated code changed together with `api/openapi.yaml` |
| `PUB-TESTS` | An existing test file is modified, deleted or renamed only if a `modify-tests:` label covers it. Test infrastructure is never changed |
| `PUB-PRAGMA` | No added line contains a skip or ignore pragma, unless an `allow:<pragma>` label is present. Pragmas: `pytest.mark.skip`, `pytest.mark.xfail`, `pytest.skip(`, `type: ignore`, `pyright: ignore`, `noqa`, `pragma: no cover`, `eslint-disable`, `biome-ignore`, `@ts-expect-error`, `@ts-ignore`, `.skip(`, `.only(` |
| `PUB-SIZE` | At most 400 changed lines (added + deleted), not counting regenerated code |
| `PUB-SECRET` | No added line matches the secret patterns or the denylist (the redaction filter's own rules) |

Out of scope for M2, noted for later: red→green on the base, and the generated-code sync check
(the sandbox already runs `scripts/gen-api.sh`).

## 2. Agent review (read-only Claude reviewer in the sandbox)
Entering `agent_review` starts a `review` run (`AGENT_MODE=review`, Sonnet, budget
`AGENT_REVIEW_BUDGET_USD` default 1.00, timeout `AGENT_REVIEW_TIMEOUT_S` default 600). The
workspace is checked out at the branch head. The reviewer is read-only: Read, Glob, Grep and
read-only git, including `git diff <base>..HEAD`.

The reviewer's context is isolated. Its `get_task` returns only:
- the card text;
- the plan's acceptance criteria;
- the base SHA, the branch and the diff stat;
- the dev run's test summary (passed/failed counts, first failing test);
- for round 2, the previous round's blockers.

It never sees the writer's steps, handoff summary or reasoning. The system prompt is the same
standards pack the writer got (AGENTS.md and `docs/standards/`).

The reviewer calls `submit_review` once. The result is validated by
`sandbox/writer/review_contract.py` and written to `/out/review.json`:

```json
{"task_id": "...", "summary": "<= 80 words",
 "findings": [{"severity": "BLOCKER|NIT", "rubric_id": "ARCH-03" | null,
               "test_id": "path::name" | null, "repro": "<= 60 words" | null,
               "path": "<changed file>", "line": 12 | null,
               "evidence": "<= 60 words", "fix": "<= 60 words"}]}
```

At most 10 findings. `path` must be a file the branch changed.

Code decides (`domain/devboard/review_policy.py`):
- A BLOCKER needs a citation: a `rubric_id` that exists in `docs/standards/review-rubric.yaml`, a
  `test_id`, or a `repro`. Without one it is downgraded to NIT.
- Duplicate findings (same `path`, `line` and `rubric_id`) are merged.
- Verdict:
  - no blockers → `approved`, and the card moves to `human_review`;
  - blockers in round 1 → `changes_requested`, and the card moves to `changes_requested`;
  - blockers in round 2 → `needs_decision`, and the card moves to `human_review` with a
    `needs-decision` label.
- Every review is stored (`task_reviews`: task, run, round, verdict, summary, findings).

## 3. Rework (`changes_requested → agent_dev`)
Entering `changes_requested` from a review starts a fresh dev run automatically (round + 1).
- It starts from the previous branch head, not from a new snapshot of the working tree.
- It has the same leases.
- Its only extra input is the blockers: `get_task` returns `review_blockers`.

It publishes a new branch, and the card goes back to `agent_review` (round 2). A human can also
send a card from `human_review` back to `changes_requested` with notes. The notes become the
blockers, and the rework starts the same way.

## 4. Human review
The card's detail page shows a **Changes** panel for cards with a branch:
- the branch name and the base SHA;
- the diff stat, and the unified diff (`GET /tasks/{id}/diff`) with one collapsible section per
  file;
- the review verdict and findings (`GET /tasks/{id}/review`): BLOCKERs first, each with its
  citation and fix;
- the dev run's test summary;
- **Download patch** (`GET /tasks/{id}/patch`, `git format-patch` output). The human applies it
  with `git am` in their own checkout. Nothing writes to the project's own repo.

Maintainer actions in `human_review`:
- **Mark done** (`human_review → done`; locally this stands in for the merge webhook).
- **Request changes** (`human_review → changes_requested`, notes required).

The card stores `branch`, `base_sha` and `review_round`.
