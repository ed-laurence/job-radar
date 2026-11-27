#!/usr/bin/env bash
# Red -> green check: the acceptance tests changed on this branch must FAIL on the base
# commit and PASS on HEAD. Run by ci.yml for agent/** branches (an independent re-check of the
# Publisher's verdict); also usable locally.
#
#   scripts/lib/red-green.sh [--base REF] [--require-tests]
#
# Acceptance tests = test files added or modified between merge-base(REF, HEAD) and HEAD:
#   backend/tests/**/test_*.py   (run with pytest, excluding integration/evals markers)
#   web/**/*.test.ts(x)          (run with vitest)
# The base is checked out in a temporary git worktree with HEAD's test files overlaid.
# --require-tests: fail when no acceptance test changed (agent branches must add tests).
set -euo pipefail
JR_SCRIPT=red-green
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

BASE_REF="origin/main" REQUIRE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE_REF="${2:?}"; shift ;;
    --require-tests) REQUIRE=1 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

cd "$JR_ROOT"
base="$(git merge-base "$BASE_REF" HEAD)" || die "no merge-base with $BASE_REF"
mapfile -t changed < <(git diff --name-only --diff-filter=AM "$base" HEAD -- \
  'backend/tests/**/test_*.py' 'web/**/*.test.ts' 'web/**/*.test.tsx')

if [[ ${#changed[@]} -eq 0 ]]; then
  if [[ $REQUIRE -eq 1 ]]; then die "no acceptance tests added or changed against $BASE_REF"; fi
  log "no acceptance tests changed; nothing to check"
  exit 0
fi
log "acceptance tests: ${changed[*]}"

run_tests() { # dir file -> exit code of the test run
  local dir="$1" f="$2"
  case "$f" in
    backend/*)
      (cd "$dir/backend" && uv sync --quiet --frozen >/dev/null 2>&1 || true
       cd "$dir/backend" && uv run --frozen pytest -q --tb=no -p no:cacheprovider \
         -m "not integration and not evals" "${f#backend/}") ;;
    web/*)
      (cd "$dir/web" && { [[ -d node_modules ]] || run_pnpm install --frozen-lockfile >/dev/null 2>&1; } \
         && run_pnpm exec vitest run "${f#web/}") ;;
  esac
}

wt="$(mktemp -d)"
cleanup() { git worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"; }
trap cleanup EXIT
git worktree add --detach --quiet "$wt" "$base"
for f in "${changed[@]}"; do
  mkdir -p "$wt/$(dirname "$f")"
  git show "HEAD:$f" > "$wt/$f"
done

bad=0
for f in "${changed[@]}"; do
  rc=0; run_tests "$wt" "$f" >"$wt/.red.log" 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then
    warn "NOT RED on base: $f passes without the change"; bad=1
  elif [[ $rc -eq 5 ]]; then
    warn "no tests collected on base: $f"; bad=1
  else
    ok "red on base: $f"
  fi
  rc=0; run_tests "$JR_ROOT" "$f" >"$wt/.green.log" 2>&1 || rc=$?
  if [[ $rc -ne 0 ]]; then
    warn "NOT GREEN on HEAD: $f (rc=$rc)"; tail -n 15 "$wt/.green.log" >&2; bad=1
  else
    ok "green on HEAD: $f"
  fi
done
exit "$bad"
