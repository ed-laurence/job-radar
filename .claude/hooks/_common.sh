# shellcheck shell=bash
# Shared helpers for .claude/hooks/*.sh. Sourced, not executed.
# Claude Code passes the hook event as JSON on stdin. Exit 2 = block, and stderr goes to Claude.

HOOK_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=../../scripts/lib/common.sh
source "$HOOK_ROOT/scripts/lib/common.sh"

HOOK_INPUT="$(cat)"

# json_get <dotted.key>: print a string/bool field from HOOK_INPUT (empty if absent).
# Carriage returns are stripped (Windows Python prints CRLF).
json_get() {
  local v
  if have jq; then
    v="$(printf '%s' "$HOOK_INPUT" | jq -r --arg k "$1" 'getpath($k | split(".")) // empty | tostring')"
  else
    v="$(printf '%s' "$HOOK_INPUT" | run_py -c '
import json, sys
try:
    v = json.load(sys.stdin)
    for part in sys.argv[1].split("."):
        v = v.get(part) if isinstance(v, dict) else None
    if v is not None:
        sys.stdout.write(str(v).lower() if isinstance(v, bool) else str(v))
except Exception:
    pass
' "$1")"
  fi
  printf '%s\n' "${v%$'\r'}"
}

# Repo-relative POSIX path for a file path from the event (absolute or relative, / or \).
rel_path() {
  local p="${1//\\//}" root="${HOOK_ROOT//\\//}"
  if is_windows && have cygpath; then
    p="$(cygpath -u "$p" 2>/dev/null || echo "$p")"; root="$(cygpath -u "$root")"
  fi
  case "$p" in
    "$root"/*) p="${p#"$root"/}" ;;
  esac
  printf '%s\n' "${p#./}"
}
