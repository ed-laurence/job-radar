#!/usr/bin/env bash
# PreToolUse hook: refuse edits to generated code and protected harness paths.
#
# Enable (by a human; agents must not edit settings) in .claude/settings.json:
#   "hooks": { "PreToolUse": [ { "matcher": "Edit|Write|MultiEdit|NotebookEdit",
#     "hooks": [ { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/protect-paths.sh\"" } ] } ] }
#
# Blocks (exit 2, reason on stderr):
#   */generated/**                     regenerate with scripts/gen-api.sh instead
#   .claude/settings*.json, .claude/hooks/**, .git/**
#   .env*, *.tfvars, lockfiles (uv.lock, pnpm-lock.yaml, .terraform.lock.hcl)
# Extra patterns: JR_PROTECTED_EXTRA="glob1:glob2" (bash globs on the repo-relative path).
# Advisory only: Bash-tool writes bypass it. The authoritative checks are check.sh and the Publisher.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

file="$(json_get tool_input.file_path)"
[[ -n "$file" ]] || file="$(json_get tool_input.notebook_path)"
[[ -n "$file" ]] || exit 0
rel="$(rel_path "$file")"

reason=""
case "$rel" in
  */generated/*|generated/*) reason="generated code; change api/openapi.yaml and run scripts/gen-api.sh" ;;
  .claude/settings.json|.claude/settings.local.json) reason="harness settings are human-only" ;;
  .claude/hooks/*) reason="harness hooks are human-only" ;;
  .git/*) reason="git internals" ;;
  .env|.env.*|*/.env|*/.env.*) reason="env files may hold secrets" ;;
  *.tfvars|*.tfvars.json) reason="tfvars are untracked and live in ~/.config/jobradar/" ;;
  uv.lock|*/uv.lock|pnpm-lock.yaml|*/pnpm-lock.yaml|*.terraform.lock.hcl) reason="lockfiles change only through uv/pnpm/tofu" ;;
esac

if [[ -z "$reason" && -n "${JR_PROTECTED_EXTRA:-}" ]]; then
  IFS=':' read -r -a extra <<<"$JR_PROTECTED_EXTRA"
  for g in "${extra[@]}"; do
    # shellcheck disable=SC2053
    [[ "$rel" == $g ]] && { reason="protected by JR_PROTECTED_EXTRA ($g)"; break; }
  done
fi

if [[ -n "$reason" ]]; then
  echo "protect-paths: refusing to edit $rel: $reason." >&2
  exit 2
fi
exit 0
