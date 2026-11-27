#!/usr/bin/env bash
# Smoke test deployed services: GET <url>/healthz must return exactly {"status":"ok"}, and the
# response must not leak denylisted identifiers (JOBRADAR_DENYLIST, when set).
#
#   scripts/lib/smoke.sh <base-url> [<base-url> ...]
# Env: SMOKE_TOKEN (optional bearer token), SMOKE_RETRIES (default 10, 6 s apart).
# URLs are never echoed (they contain project-specific hostnames); services are numbered.
set -euo pipefail
JR_SCRIPT=smoke
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

[[ $# -gt 0 ]] || die "usage: smoke.sh <base-url> [...]"
retries="${SMOKE_RETRIES:-10}"
auth=(); [[ -n "${SMOKE_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $SMOKE_TOKEN")
fail=0 n=0
for base in "$@"; do
  n=$((n + 1))
  body="" code=""
  for ((i = 1; i <= retries; i++)); do
    resp="$(curl -sS -m 10 "${auth[@]}" -w '\n%{http_code}' "${base%/}/healthz" 2>/dev/null || true)"
    code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
    [[ "$code" == 200 ]] && break
    sleep 6
  done
  compact="$(printf '%s' "$body" | tr -d ' \n\r\t')"
  if [[ "$code" != 200 || "$compact" != '{"status":"ok"}' ]]; then
    warn "service #$n: /healthz failed (http $code)"; fail=1; continue
  fi
  if [[ -n "${JOBRADAR_DENYLIST:-}" && -s "$JOBRADAR_DENYLIST" ]]; then
    printf '%s' "$body" | run_py "$JR_ROOT/scripts/lib/denylist_scan.py" "$JOBRADAR_DENYLIST" --stdin-text >/dev/null \
      || { warn "service #$n: /healthz leaks a denylisted value"; fail=1; continue; }
  fi
  ok "service #$n: /healthz ok"
done
exit "$fail"
