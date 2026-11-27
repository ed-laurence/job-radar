#!/usr/bin/env bash
# Run one writer-sandbox task with the hardening flags. This is the reference invocation the
# local runner must reproduce exactly (docs/agents.md "Local sandbox").
#
#   sandbox/run.sh --task-id ID --in DIR --out DIR [--image IMG]
#                  [--entrypoint PATH] [--arg A]... [-- extra docker-run args (mounts, -e NAME)]
#
#   DIR given to --in must contain input.bundle; --out receives result.bundle + handoff.json.
# Env passed through by NAME only (values never on the command line or in logs):
#   ANTHROPIC_AUTH_TOKEN   per-task llm-proxy token (python -m llm_proxy.mint)
#   COORDINATOR_TOKEN      per-task coordinator token (jrc1)
#   RUN_ID, AGENT_MODE (agent|plan|triage|stub), AGENT_MODEL, AGENT_MAX_TURNS, AGENT_TOKEN_BUDGET,
#   AGENT_TIMEOUT_S, TASK_BASE_REF, SANDBOX_SUITES, SANDBOX_POSTGRES
# COORDINATOR_URL (HTTP API base) comes from AGENT_COORDINATOR_URL (default http://coordinator:8100),
# ANTHROPIC_BASE_URL from AGENT_LLM_BASE_URL (http://llm-proxy:8080); both are reached directly on the internal network.
set -euo pipefail

IMAGE="${AGENT_SANDBOX_IMAGE:-jobradar-sandbox-writer:dev}"
NETWORK="${AGENT_SANDBOX_NETWORK:-jobradar_sandbox}"
PROXY="${AGENT_EGRESS_PROXY:-http://egress-proxy:3128}"
LLM_URL="${AGENT_LLM_BASE_URL:-http://llm-proxy:8080}"
COORD_URL="${AGENT_COORDINATOR_URL:-${COORDINATOR_URL:-http://coordinator:8100}}"
TASK_ID="" IN_DIR="" OUT_DIR="" EXTRA=() ENTRY=() ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="${2:?}"; shift ;;
    --in) IN_DIR="${2:?}"; shift ;;
    --out) OUT_DIR="${2:?}"; shift ;;
    --image) IMAGE="${2:?}"; shift ;;
    --entrypoint) ENTRY=(--entrypoint "${2:?}"); shift ;;
    --arg) ARGS+=("${2?}"); shift ;;
    --) shift; EXTRA=("$@"); break ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -n "$TASK_ID" && -n "$IN_DIR" && -n "$OUT_DIR" ]] || { echo "need --task-id, --in and --out" >&2; exit 2; }
[[ -f "$IN_DIR/input.bundle" ]] || { echo "$IN_DIR/input.bundle not found" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker not installed" >&2; exit 127; }
mkdir -p "$OUT_DIR"

# Git Bash would rewrite /in, /out... into Windows paths; docker needs them verbatim.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
host_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$(cd "$1" && pwd)"; else (cd "$1" && pwd); fi
}

env_args=(-e TASK_ID="$TASK_ID"
  -e HTTPS_PROXY="$PROXY" -e HTTP_PROXY="$PROXY" -e https_proxy="$PROXY" -e http_proxy="$PROXY"
  -e NO_PROXY="localhost,127.0.0.1,llm-proxy,coordinator"
  -e no_proxy="localhost,127.0.0.1,llm-proxy,coordinator"
  -e ANTHROPIC_BASE_URL="$LLM_URL" -e COORDINATOR_URL="$COORD_URL")
for name in ANTHROPIC_AUTH_TOKEN COORDINATOR_TOKEN RUN_ID AGENT_MODE AGENT_MODEL AGENT_MAX_TURNS \
  AGENT_TOKEN_BUDGET AGENT_TIMEOUT_S TASK_BASE_REF SANDBOX_SUITES SANDBOX_POSTGRES; do
  if [[ -n "${!name:-}" ]]; then env_args+=(-e "$name"); fi
done

exec docker run --rm \
  --name "jr-sandbox-${TASK_ID//[^A-Za-z0-9_.-]/-}" \
  --network "$NETWORK" \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --tmpfs /workspace:rw,exec,nosuid,nodev,size=4g,uid=10001,gid=10001,mode=0700 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 10001:10001 \
  --pids-limit 1024 --memory 6g --cpus 2 \
  --mount "type=bind,source=$(host_path "$IN_DIR"),target=/in,readonly" \
  --mount "type=bind,source=$(host_path "$OUT_DIR"),target=/out" \
  "${env_args[@]}" \
  "${ENTRY[@]+"${ENTRY[@]}"}" \
  "${EXTRA[@]+"${EXTRA[@]}"}" \
  "$IMAGE" "${ARGS[@]+"${ARGS[@]}"}"
