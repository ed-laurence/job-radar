#!/usr/bin/env bash
# Run Job Radar locally: database, migrations, seed, codegen, API and web.
#
#   scripts/dev.sh [--reset-db] [--no-web] [--with-agents]
#
# Modes (environment variables):
#   LLM=vertex|replay                 vertex (default) = live models (Claude via CLAUDE_BACKEND=vertex|anthropic);
#                                     replay = recorded fixtures, no network (opt-in)
#   GUARDS=fake|gcp                   fake = in-process ContentGuard/PII fakes
#   REVIEW=claude-only|cross-model    in-app review panel
# Flags:
#   --reset-db      wipe the local database first (scripts/dev-db.sh --reset)
#   --no-web        API only
#   --with-agents   dev-board agents: Docker sandbox + LocalGitHost
#                   (scratch bare repo in ~/.cache/jobradar/agent-remote; project git is never touched)
#
# API: http://127.0.0.1:8000   Web: http://127.0.0.1:3000   Ctrl-C stops both.
# The database keeps running afterwards; stop it with scripts/dev-db.sh --stop.
# Runs in bash (Git Bash or WSL on Windows).
set -euo pipefail
JR_SCRIPT=dev
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

RESET_DB=0 NO_WEB=0 WITH_AGENTS=0
for arg in "$@"; do
  case "$arg" in
    --reset-db) RESET_DB=1 ;;
    --no-web) NO_WEB=1 ;;
    --with-agents) WITH_AGENTS=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

# Optional private overrides OUTSIDE the repo (e.g. ANTHROPIC_API_KEY, CLAUDE_BACKEND, LLM,
# GOOGLE_CLOUD_PROJECT). One KEY=value per line; never commit or copy it into the repo.
local_env="$HOME/.config/jobradar/local.env"
if [[ -f "$local_env" ]]; then
  # Modes given on the command line (e.g. `LLM=replay bash scripts/dev.sh` for e2e) beat the file.
  cli_llm="${LLM-}" cli_guards="${GUARDS-}" cli_review="${REVIEW-}"
  set -a
  # shellcheck disable=SC1090
  . "$local_env"
  set +a
  [[ -n "$cli_llm" ]] && LLM="$cli_llm"
  [[ -n "$cli_guards" ]] && GUARDS="$cli_guards"
  [[ -n "$cli_review" ]] && REVIEW="$cli_review"
  log "loaded private overrides from ~/.config/jobradar/local.env (values not shown)"
fi
export LLM="${LLM:-vertex}" GUARDS="${GUARDS:-fake}" REVIEW="${REVIEW:-claude-only}"
# Live by default. Only with LLM=replay (opt-in): prompts with no recording (new roles/tasks you
# create) get labelled synthetic output instead of failing. Tests and CI keep the strict default.
if [[ "$LLM" == replay ]]; then export LLM_REPLAY_MISS="${LLM_REPLAY_MISS:-synthesize}"; else unset LLM_REPLAY_MISS; fi
# Local only: a full e2e run needs ~215 calls/min against the 120/min DEFAULT tier. Prod is pinned
# to 1 by the settings validator.
export RATE_LIMIT_SCALE="${RATE_LIMIT_SCALE:-10}"
# Local only: the API runs the simulated scan in-process every N minutes (prod uses Cloud Scheduler).
export SCAN_INTERVAL_MINUTES="${SCAN_INTERVAL_MINUTES:-15}"
case "$LLM" in vertex|replay) ;; *) die "LLM must be vertex or replay (got: $LLM)" ;; esac
case "$GUARDS" in fake|gcp) ;; *) die "GUARDS must be fake or gcp (got: $GUARDS)" ;; esac
case "$REVIEW" in claude-only|cross-model) ;; *) die "REVIEW must be claude-only or cross-model (got: $REVIEW)" ;; esac

cd "$JR_ROOT"
HAS_BACKEND=0; [[ -f backend/pyproject.toml ]] && HAS_BACKEND=1
HAS_WEB=0; [[ -f web/package.json && $NO_WEB -eq 0 ]] && HAS_WEB=1

# ------------------------------------------------------------ 1. prerequisites
missing=()
have git || missing+=("git")
have uv || missing+=("uv (https://docs.astral.sh/uv/)")
if [[ $HAS_WEB -eq 1 ]]; then
  have node || missing+=("node 20+")
  pnpm_cmd >/dev/null || missing+=("pnpm (or corepack / npx)")
fi
if [[ $WITH_AGENTS -eq 1 ]] && ! docker_ok; then
  missing+=("docker (required by --with-agents for the agent sandbox)")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  die "missing prerequisites: ${missing[*]}"
fi
export CLAUDE_BACKEND="${CLAUDE_BACKEND:-vertex}"
needs_gcp=0
[[ "$LLM" == vertex && "$CLAUDE_BACKEND" == vertex ]] && needs_gcp=1
[[ "$GUARDS" == gcp || "$REVIEW" == cross-model ]] && needs_gcp=1
if [[ "$LLM" == vertex && "$CLAUDE_BACKEND" == anthropic ]]; then
  # Local only: live Claude via the first-party API while Vertex quota is pending.
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "CLAUDE_BACKEND=anthropic needs ANTHROPIC_API_KEY in your environment (never in a file in the repo)"
fi
if [[ $needs_gcp -eq 1 ]]; then
  if ! have gcloud || ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    die "LLM=vertex / GUARDS=gcp / REVIEW=cross-model need Application Default Credentials: gcloud auth application-default login"
  fi
  [[ -n "${GOOGLE_CLOUD_PROJECT:-}" ]] || die "set GOOGLE_CLOUD_PROJECT (from ~/.config/jobradar/, never committed) for online modes"
  export GCP_PROJECT="${GCP_PROJECT:-$GOOGLE_CLOUD_PROJECT}"
fi
log "modes: LLM=$LLM GUARDS=$GUARDS REVIEW=$REVIEW agents=$WITH_AGENTS"

# Local sign-in allowlist (fake auth). Real emails live only in the untracked config file;
# otherwise the two placeholder seed accounts are used. An empty allowlist denies everyone.
export AUTH="${AUTH:-fake}"
if [[ -z "${APP_USERS:-}" && -z "${APP_USERS_FILE:-}" ]]; then
  users_file="$HOME/.config/jobradar/users.json"
  if [[ -f "$users_file" ]]; then
    export APP_USERS_FILE="$users_file"
  else
    export APP_USERS='[{"email":"owner@example.test","role":"maintainer"},{"email":"tester@example.test","role":"user"}]'
  fi
fi
log "auth: AUTH=$AUTH allowlist=$( [[ -n "${APP_USERS_FILE:-}" ]] && echo file || { [[ -n "${APP_USERS:-}" ]] && echo env || echo EMPTY; } )"

# ------------------------------------------------------------ 2. services
if docker_ok; then
  # The egress proxy only matters for sandboxed agents, so a plain run doesn't depend on it.
  services=(fake-gcs); [[ $WITH_AGENTS -eq 1 ]] && services+=(egress-proxy)
  docker compose -f docker-compose.yml up -d --wait "${services[@]}" >&2
  export STORAGE_EMULATOR_HOST="http://127.0.0.1:4443"
  log "fake GCS on 127.0.0.1:4443$( [[ $WITH_AGENTS -eq 1 ]] && echo ', egress proxy on 127.0.0.1:3128')"
else
  warn "Docker not available: no fake GCS or egress proxy. The backend falls back to its local storage adapter."
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  db_args=(); [[ $RESET_DB -eq 1 ]] && db_args+=(--reset)
  db_line="$("$JR_ROOT/scripts/dev-db.sh" "${db_args[@]}")"
  export DATABASE_URL="${db_line#DATABASE_URL=}"
else
  [[ $RESET_DB -eq 1 ]] && die "--reset-db only works with the scripts/dev-db.sh database; unset DATABASE_URL"
  log "using DATABASE_URL from the environment"
fi
log "DATABASE_URL=$(printf '%s' "$DATABASE_URL" | sed -E 's#//[^@/]*@#//***@#')"

# ------------------------------------------------------------ 3. backend: sync, migrate, seed
if [[ $HAS_BACKEND -eq 1 ]]; then
  (cd backend && uv sync) || die "uv sync failed in backend/"
  (cd backend && uv run alembic upgrade head) || die "alembic upgrade head failed (is backend/alembic.ini configured?)"
  (cd backend && uv run python -m app.jobs.seed) || die "seed failed: uv run python -m app.jobs.seed"
  ok "backend synced, migrated and seeded"
  # The API serves as a restricted login, never the admin/superuser URL (SEC-03: RLS bypass).
  ADMIN_DATABASE_URL="$DATABASE_URL"
  APP_DATABASE_URL="$(cd backend && uv run python ../scripts/lib/dev_app_login.py "$ADMIN_DATABASE_URL")" \
    || die "could not create the restricted API login (scripts/lib/dev_app_login.py)"
  ok "API will connect as the restricted login jobradar_dev_app (RLS enforced)"
else
  log "SKIP backend setup: backend/ does not exist yet"
fi

# ------------------------------------------------------------ 4. codegen
gen_rc=0
"$JR_ROOT/scripts/gen-api.sh" || gen_rc=$?
case $gen_rc in
  0) ;;
  3) log "SKIP codegen: no spec or no backend/web yet" ;;
  *) die "scripts/gen-api.sh failed" ;;
esac

# ------------------------------------------------------------ 5. agents (opt-in)
if [[ $WITH_AGENTS -eq 1 ]]; then
  remote="$JR_CACHE_DIR/agent-remote"
  if [[ ! -d "$remote" ]]; then
    mkdir -p "$remote" && git init --bare --quiet "$remote"
    log "created scratch agent remote at $remote"
  fi
  # HMAC keys, generated once outside the repo and never printed:
  #   llm-proxy.secret   -> LLM_PROXY_SECRET       (per-task jrt1 tokens for llm-proxy)
  #   coordinator.secret -> COORDINATOR_TOKEN_KEY  (per-task jrc1 tokens for the coordinator)
  agent_secret() { # file name -> prints the value (to a variable, never the terminal)
    local file="$JR_CONFIG_DIR/$1"
    if [[ ! -s "$file" ]]; then
      mkdir -p "$JR_CONFIG_DIR"
      (umask 077 && run_py -c 'import secrets; print(secrets.token_hex(32))' > "$file") \
        || die "could not generate ~/.config/jobradar/$1"
      log "generated ~/.config/jobradar/$1 (value not shown)"
    fi
    chmod 600 "$file" 2>/dev/null || true
    tr -d '[:space:]' < "$file"
  }
  LLM_PROXY_SECRET="$(agent_secret llm-proxy.secret)"
  COORDINATOR_TOKEN_KEY="$(agent_secret coordinator.secret)"
  [[ -n "$LLM_PROXY_SECRET" && -n "$COORDINATOR_TOKEN_KEY" ]] || die "empty agent secret in ~/.config/jobradar"
  # The writer reaches Claude only through llm-proxy, so by default squid allows no external
  # model host at all (AGENT_MODEL_ENDPOINT=vertex re-enables *.googleapis.com).
  AGENT_MODEL_ENDPOINT="${AGENT_MODEL_ENDPOINT:-llm-proxy}"
  export LLM_PROXY_SECRET COORDINATOR_TOKEN_KEY AGENT_MODEL_ENDPOINT
  # The coordinator container reaches Postgres on the compose network (postgres:5432) as the
  # restricted login, so RLS applies; never the admin URL.
  if [[ -n "${APP_DATABASE_URL:-}" ]]; then
    COORDINATOR_DATABASE_URL="$(printf '%s' "$APP_DATABASE_URL" | sed -E 's#@(127\.0\.0\.1|localhost):[0-9]+/#@postgres:5432/#')"
    export COORDINATOR_DATABASE_URL
  else
    warn "no restricted app login yet; the coordinator will start without a database"
  fi
  export CODE_HOST=local AGENT_SANDBOX=docker AGENT_REMOTE_PATH="$remote"
  export AGENT_EGRESS_PROXY="http://egress-proxy:3128" AGENT_SANDBOX_NETWORK="jobradar_sandbox"
  export AGENT_LLM_BASE_URL="http://llm-proxy:8080" AGENT_SANDBOX_IMAGE="jobradar-sandbox-writer:dev"
  export AGENT_SANDBOX_RUN="$JR_ROOT/sandbox/run.sh"

  compose_agents=(docker compose --progress=plain -f docker-compose.yml --profile agents)
  has_coordinator=0
  "${compose_agents[@]}" config --services 2>/dev/null | grep -qx coordinator && has_coordinator=1

  # Full (non-quiet) build output goes to a log: quiet builds used to hide the errors.
  mkdir -p "$JR_ARTIFACTS"
  build_log="$JR_ARTIFACTS/agent-build.log"
  : > "$build_log"
  build_failed() {
    warn "last lines of .artifacts/agent-build.log:"
    tail -n 25 "$build_log" >&2
    die "$1 (full log: .artifacts/agent-build.log)"
  }
  log "building the sandbox images (first build takes several minutes; log: .artifacts/agent-build.log)"
  docker build --progress=plain -f sandbox/writer/Dockerfile -t "$AGENT_SANDBOX_IMAGE" . >>"$build_log" 2>&1 \
    || build_failed "sandbox writer image build failed"
  build_services=(llm-proxy); [[ $has_coordinator -eq 1 ]] && build_services+=(coordinator)
  "${compose_agents[@]}" build "${build_services[@]}" >>"$build_log" 2>&1 \
    || build_failed "image build failed for: ${build_services[*]}"
  ok "built $AGENT_SANDBOX_IMAGE and ${build_services[*]}"

  agent_services=(egress-proxy)
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    agent_services+=(llm-proxy)
  else
    warn "ANTHROPIC_API_KEY not set: llm-proxy not started (agent runs will fail at the model call)"
  fi
  if [[ $has_coordinator -eq 1 ]]; then
    agent_services+=(coordinator)
  else
    warn "docker-compose.yml has no coordinator service yet: not started (agent runs fail closed at get_task)"
  fi
  "${compose_agents[@]}" up -d --wait "${agent_services[@]}" >&2 \
    || die "could not start ${agent_services[*]}"
  # A running squid keeps the config it started with; re-read the (possibly edited) allowlist.
  "${compose_agents[@]}" exec -T egress-proxy squid -k reconfigure >/dev/null 2>&1 \
    || warn "could not reload the egress-proxy config (restart it: docker compose restart egress-proxy)"
  ok "agents: ${agent_services[*]} up (model endpoint: $AGENT_MODEL_ENDPOINT)"
  log "agent env exported: CODE_HOST AGENT_SANDBOX AGENT_REMOTE_PATH AGENT_EGRESS_PROXY AGENT_SANDBOX_NETWORK AGENT_LLM_BASE_URL AGENT_SANDBOX_IMAGE AGENT_SANDBOX_RUN AGENT_MODEL_ENDPOINT LLM_PROXY_SECRET COORDINATOR_TOKEN_KEY COORDINATOR_DATABASE_URL (values not shown)"
fi

# ------------------------------------------------------------ 6. run api + web
pids=()
cleanup() {
  trap - INT TERM EXIT
  if [[ ${#pids[@]} -gt 0 ]]; then
    log "stopping..."
    for p in "${pids[@]}"; do kill_tree "$p"; done
    wait 2>/dev/null || true
  fi
  log "stopped. The database is still running (scripts/dev-db.sh --stop)."
}
trap cleanup INT TERM EXIT

if [[ $HAS_BACKEND -eq 1 ]]; then
  (cd backend && DATABASE_URL="${APP_DATABASE_URL:-$DATABASE_URL}" exec uv run uvicorn "${APP_MODULE:-app.main:create_app}" --factory --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 3) &
  pids+=($!)
  log "api -> http://127.0.0.1:8000"
fi

if [[ $NO_WEB -eq 1 ]]; then
  log "SKIP web: --no-web"
elif [[ $HAS_WEB -eq 1 ]]; then
  [[ -d web/node_modules ]] || (cd web && run_pnpm install --frozen-lockfile)
  export API_BASE_URL="http://127.0.0.1:8000"
  (cd web && run_pnpm exec next dev --hostname 127.0.0.1 --port 3000) &
  pids+=($!)
  log "web -> http://127.0.0.1:3000"
else
  log "SKIP web: web/ does not exist yet"
fi

if [[ ${#pids[@]} -eq 0 ]]; then
  log "nothing to run yet (no backend/ or web/). The database is up."
  exit 0
fi

# Exit when either process dies (or on Ctrl-C); cleanup stops the other.
set +e
wait -n "${pids[@]}"
rc=$?
log "a process exited (rc=$rc)"
exit "$rc"
