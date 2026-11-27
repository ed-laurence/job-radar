#!/usr/bin/env bash
# PostToolUse hook: lint and type-check the package of the file that was just edited.
#
# Enable (by a human; agents must not edit settings) in .claude/settings.json:
#   "hooks": { "PostToolUse": [ { "matcher": "Edit|Write|MultiEdit",
#     "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/post-edit-lint.sh\"", "timeout": 180 } ] } ] }
#
# backend/**/*.py      ruff check + ruff format --check on the file, pyright on its package dir
# web/**/*.{ts,tsx,..} biome check on the file, tsc --noEmit for web/
# infra/**/*.tf        tofu (or terraform) fmt -check on the file
# api/openapi.yaml     scripts/check.sh --only spectral,gen-api
# .claude/agents/*.md  scripts/check.sh --only agent-lint
# Problems: exit 2 with the first lines of output on stderr (Claude sees them). Missing tools: silent.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

file="$(json_get tool_input.file_path)"
[[ -n "$file" ]] || exit 0
rel="$(rel_path "$file")"
cd "$HOOK_ROOT"
[[ -f "$rel" ]] || exit 0

out="$(mktemp)"; trap 'rm -f "$out"' EXIT
rc=0
case "$rel" in
  backend/*.py)
    [[ -f backend/pyproject.toml ]] && have uv || exit 0
    inner="${rel#backend/}"; pkg="$(dirname "$inner")"
    {
      uv_backend ruff check --output-format concise "$inner" &&
      uv_backend ruff format --check "$inner" &&
      uv_backend pyright "$pkg"
    } >"$out" 2>&1 || rc=$?
    ;;
  web/*.ts|web/*.tsx|web/*.js|web/*.jsx|web/*.mjs|web/*.cjs|web/*.json|web/*.css)
    [[ -d web/node_modules ]] || exit 0
    inner="${rel#web/}"
    {
      (cd web && run_pnpm exec biome check "$inner") &&
      (cd web && run_pnpm exec tsc --noEmit)
    } >"$out" 2>&1 || rc=$?
    ;;
  infra/*.tf|infra/*.tftest.hcl)
    iac="$(iac_cmd)" || exit 0
    "$iac" fmt -check -diff "$rel" >"$out" 2>&1 || rc=$?
    ;;
  api/openapi.yaml)
    scripts/check.sh --only spectral,gen-api >"$out" 2>&1 || rc=$?
    ;;
  .claude/agents/*.md)
    scripts/check.sh --only agent-lint >"$out" 2>&1 || rc=$?
    ;;
  *) exit 0 ;;
esac

if [[ $rc -ne 0 ]]; then
  echo "post-edit-lint: problems after editing $rel (fix before continuing):" >&2
  head -n 20 "$out" >&2
  exit 2
fi
exit 0
