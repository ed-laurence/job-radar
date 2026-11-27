#!/usr/bin/env bash
# Stop hook: block "done" while the fast test suite of a touched package fails.
#
# Enable (by a human; agents must not edit settings) in .claude/settings.json:
#   "hooks": { "Stop": [ { "hooks": [ { "type": "command",
#     "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-tests.sh\"", "timeout": 600 } ] } ] }
#
# Touched packages come from `git status --porcelain` (works before the first commit):
#   backend/  ->  pytest -q -m "not integration and not evals" -x
#   web/      ->  vitest run
# Failing tests: exit 2 with the first lines on stderr, so Claude keeps working.
# To avoid an endless loop, it blocks at most 3 times in a row (counter in .artifacts/hooks/),
# then lets the stop through with a warning. The counter resets when tests pass.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

cd "$HOOK_ROOT"
state_dir="$JR_ARTIFACTS/hooks"; mkdir -p "$state_dir"
counter="$state_dir/stop-blocks"

changed="$(git status --porcelain --untracked-files=all 2>/dev/null | sed -E 's/^.{3}//; s/^.* -> //')"
touched_backend=0; touched_web=0
grep -q '^backend/' <<<"$changed" && touched_backend=1
grep -q '^web/' <<<"$changed" && touched_web=1

out="$(mktemp)"; trap 'rm -f "$out"' EXIT
fail=""
if [[ $touched_backend -eq 1 && -f backend/pyproject.toml ]] && have uv; then
  rc=0
  uv_backend pytest -q --tb=short -x -m "not integration and not evals" -p no:cacheprovider >"$out" 2>&1 || rc=$?
  # rc 5 = no tests collected; not a failure of existing tests.
  [[ $rc -ne 0 && $rc -ne 5 ]] && fail="backend"
fi
if [[ -z "$fail" && $touched_web -eq 1 && -d web/node_modules ]]; then
  rc=0
  (cd web && run_pnpm exec vitest run) >"$out" 2>&1 || rc=$?
  [[ $rc -ne 0 ]] && ! grep -q 'No test files found' "$out" && fail="web"
fi

if [[ -z "$fail" ]]; then
  rm -f "$counter"
  exit 0
fi

n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
if [[ $n -gt 3 ]]; then
  rm -f "$counter"
  echo "stop-tests: $fail tests still failing after 3 attempts; stopping anyway. Report the failure in the handoff." >&2
  exit 0
fi
echo "stop-tests: the $fail fast suite fails ($n/3). Fix it before reporting done:" >&2
tail -n 25 "$out" >&2
exit 2
