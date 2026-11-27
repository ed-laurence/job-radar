#!/usr/bin/env bash
# Writer sandbox entrypoint.
#
# In:  /in/input.bundle (git bundle of the base commit; read-only mount)
# Out: /out/result.bundle (branch agent/<TASK_ID>) and /out/handoff.json
# Env: TASK_ID (required), TASK_BASE_REF (default HEAD), SANDBOX_POSTGRES=1|0,
#      SANDBOX_SUITES (default "backend,web"), AGENT_MODE=agent|stub (default agent)
#      agent mode also needs: ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, COORDINATOR_URL,
#      COORDINATOR_TOKEN, RUN_ID; optional AGENT_MODEL, AGENT_MAX_TURNS, AGENT_TOKEN_BUDGET,
#      AGENT_TIMEOUT_S (wall clock for the agent loop, default 3600)
#
# AGENT_MODE=agent runs the Claude Agent SDK loop (agent_runner.py): the task comes from the
# coordinator's get_task, the system prompt from AGENTS.md + docs/standards/ at the base commit.
# AGENT_MODE=stub makes no edits and only runs the repo's tests (isolation plumbing test).
# Either way the repo tests are then re-run here, whatever changed is committed on the branch,
# and result.bundle + handoff.json are written. Exit 0 when the plumbing worked (outcomes live
# in handoff.json); non-zero only when the plumbing itself failed.
set -euo pipefail

IN=/in OUT=/out WORK=/workspace/repo RESULTS=/tmp/results
log() { printf '[sandbox] %s\n' "$*" >&2; }

TASK_ID="${TASK_ID:-}"
[[ "$TASK_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { log "TASK_ID missing or invalid"; exit 2; }
BASE_REF="${TASK_BASE_REF:-HEAD}"
SUITES="${SANDBOX_SUITES:-backend,web}"
BRANCH="agent/$TASK_ID"
SKIPPED=()

fail_handoff() {
  # Plumbing failure: still leave a machine-readable handoff if /out is writable.
  local reason="$1"
  /usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --status error --summary "$reason" \
    --out "$OUT/handoff.json" || true
  log "error: $reason"
  exit 1
}

[[ "$(id -u)" != 0 ]] || fail_handoff "refusing to run as root"
[[ -f "$IN/input.bundle" ]] || fail_handoff "missing /in/input.bundle"
[[ -w "$OUT" ]] || { log "error: /out is not writable"; exit 1; }
mkdir -p "$HOME" "$RESULTS" /tmp/vite-cache /tmp/uv-cache

# Git: no global hooks, no credential helpers, fixed identity, repo treated as data.
export GIT_CONFIG_GLOBAL="$HOME/.gitconfig" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
git config --global user.name "jobradar-agent"
git config --global user.email "agent@example.test"
git config --global core.hooksPath /dev/null
git config --global init.defaultBranch main
git config --global safe.directory "$WORK"

# ------------------------------------------------------------ 1. input bundle -> workspace
# `git bundle verify` needs a repository to run in, so verify from an empty scratch one.
verify_repo="$(mktemp -d)"
git -C "$verify_repo" init -q
git -C "$verify_repo" bundle verify -q "$IN/input.bundle" >/dev/null 2>&1 \n  || { rm -rf "$verify_repo"; fail_handoff "input bundle failed verification"; }
rm -rf "$verify_repo" "$WORK"
git clone --quiet --no-checkout "$IN/input.bundle" "$WORK" || fail_handoff "clone from bundle failed"
cd "$WORK"
base_sha="$(git rev-parse --verify "${BASE_REF}^{commit}" 2>/dev/null)" || fail_handoff "base ref not in bundle"
git checkout --quiet -B "$BRANCH" "$base_sha"
log "workspace at $BRANCH ($(git rev-parse --short HEAD))"

# Baked dependencies: link, never install. The link is never committed.
printf '%s\n' '/web/node_modules' >> .git/info/exclude
if [[ -d web && ! -e web/node_modules ]]; then ln -s /opt/deps/web/node_modules web/node_modules; fi

# ------------------------------------------------------------ 2. Postgres (inside the container)
PGDATA=/tmp/pgdata PGSOCK=/tmp/pgsock
pg_stop() { [[ -f "$PGDATA/postmaster.pid" ]] && pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true; }
trap pg_stop EXIT
if [[ "${SANDBOX_POSTGRES:-1}" == 1 ]]; then
  mkdir -p "$PGSOCK"
  initdb -D "$PGDATA" -U sandbox --auth=trust --encoding=UTF8 --no-instructions >/tmp/pg-init.log 2>&1 \
    || fail_handoff "initdb failed"
  pg_ctl -D "$PGDATA" -l /tmp/pg.log -w -o "-c listen_addresses=127.0.0.1 -c port=5432 -c unix_socket_directories=$PGSOCK -c fsync=off" start >/dev/null \
    || fail_handoff "postgres failed to start"
  export DATABASE_URL="postgresql+asyncpg://sandbox@127.0.0.1:5432/postgres"
  log "postgres 16 on 127.0.0.1:5432"
else
  SKIPPED+=("pytest-integration: SANDBOX_POSTGRES=0")
fi

# ------------------------------------------------------------ 3. agent
AGENT_MODE="${AGENT_MODE:-agent}"
AGENT_DIR=/tmp/agent
agent_args=()
case "$AGENT_MODE" in
  stub) log "agent: stub (no edits)" ;;
  agent)
    for name in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN COORDINATOR_URL COORDINATOR_TOKEN RUN_ID; do
      [[ -n "${!name:-}" ]] || fail_handoff "AGENT_MODE=agent needs $name"
    done
    timeout_s="${AGENT_TIMEOUT_S:-3600}"
    [[ "$timeout_s" =~ ^[0-9]{2,5}$ ]] || fail_handoff "AGENT_TIMEOUT_S must be 10-99999 seconds"
    # The prompt pack comes from the BASE commit, extracted before the agent can touch anything.
    mkdir -p "$AGENT_DIR/base"
    git archive --format=tar "$base_sha" -- AGENTS.md docs/standards 2>/dev/null \
      | tar -x -C "$AGENT_DIR/base" 2>/dev/null \
      || fail_handoff "AGENTS.md or docs/standards/ missing at the base commit"
    log "agent: claude agent sdk (timeout ${timeout_s}s)"
    # timeout signals the whole process group, so the CLI child dies with the runner.
    agent_rc=0
    timeout --kill-after=20 "$timeout_s" /opt/agent/venv/bin/python /opt/sandbox/agent_runner.py \
      --repo "$WORK" --base-dir "$AGENT_DIR/base" --out "$AGENT_DIR/result.json" || agent_rc=$?
    case $agent_rc in
      0) ;;
      124|137) log "agent: wall-clock timeout"; SKIPPED+=("agent: stopped at AGENT_TIMEOUT_S") ;;
      3) log "agent: setup failed (see above)" ;;
      *) log "agent: runner exited $agent_rc" ;;
    esac
    if [[ $agent_rc -ne 0 && -s "$AGENT_DIR/cli.log" ]]; then
      log "agent: last CLI stderr lines:"; tail -n 15 "$AGENT_DIR/cli.log" >&2 || true
    fi
    cd "$WORK"
    agent_args=(--agent-mode --agent-result "$AGENT_DIR/result.json")
    ;;
  *) fail_handoff "unknown AGENT_MODE (agent|stub)" ;;
esac

# ------------------------------------------------------------ 4. tests (JSON reports only; logs stay in /tmp)
report_args=()
if [[ ",$SUITES," == *",backend,"* ]]; then
  if [[ -f backend/pyproject.toml ]]; then
    marker="not evals"; [[ -n "${DATABASE_URL:-}" ]] || marker="not integration and not evals"
    (cd backend && SANDBOX_PYTEST_REPORT="$RESULTS/backend.json" PYTHONPATH=/opt/sandbox \
      python -m pytest -q --tb=short -p no:cacheprovider -p pytest_report -m "$marker") \
      >/tmp/backend-tests.log 2>&1 || true
    if [[ -f "$RESULTS/backend.json" ]]; then report_args+=(--pytest-json "$RESULTS/backend.json")
    else SKIPPED+=("pytest: produced no report"); fi
  else
    SKIPPED+=("pytest: no backend/ in the bundle")
  fi
fi
if [[ ",$SUITES," == *",web,"* ]]; then
  if [[ -f web/package.json && -x web/node_modules/.bin/vitest ]]; then
    (cd web && node_modules/.bin/vitest run --reporter=default --reporter=json \
      --outputFile.json="$RESULTS/web.json") >/tmp/web-tests.log 2>&1 || true
    if [[ -f "$RESULTS/web.json" ]]; then report_args+=(--vitest-json "$RESULTS/web.json")
    else SKIPPED+=("vitest: produced no report"); fi
  else
    SKIPPED+=("vitest: no web/ in the bundle")
  fi
fi

# ------------------------------------------------------------ 5. result bundle + handoff
# Whatever the agent left (committed or not) ends up on the branch; HEAD must stay on it.
git checkout --quiet "$BRANCH" 2>/dev/null || fail_handoff "agent left the task branch"
git add -A
git diff --cached --quiet || git commit --quiet --no-verify -m "agent($TASK_ID): changes"
git bundle create "$OUT/result.bundle" "$BRANCH" >/dev/null 2>&1 || fail_handoff "result bundle failed"

skip_args=()
for s in "${SKIPPED[@]+"${SKIPPED[@]}"}"; do skip_args+=(--skipped "$s"); done
git diff --name-only "$base_sha..HEAD" > /tmp/changed-paths.txt
/usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --changed /tmp/changed-paths.txt \
  "${report_args[@]+"${report_args[@]}"}" "${skip_args[@]+"${skip_args[@]}"}" \
  "${agent_args[@]+"${agent_args[@]}"}" --out "$OUT/handoff.json" || fail_handoff "handoff writer failed"
log "wrote /out/result.bundle and /out/handoff.json"
