#!/usr/bin/env bash
# Writer sandbox entrypoint.
#
# In:  /in/input.bundle (git bundle of the base commit, or of a published branch head for
#      review and rework runs; read-only mount)
# Out: /out/result.bundle (branch agent/<TASK_ID>) and /out/handoff.json; in agent mode also
#      /out/agent.log (one metadata line per model turn) and /out/steps.jsonl (posted steps)
# Env: TASK_ID (required), TASK_BASE_REF (default HEAD), SANDBOX_POSTGRES=1|0,
#      SANDBOX_SUITES (default "backend,web"),
#      AGENT_MODE=agent|plan|triage|review|stub (default agent)
#      agent mode also needs: ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, COORDINATOR_URL,
#      COORDINATOR_TOKEN, RUN_ID; optional AGENT_MODEL, AGENT_MAX_TURNS, AGENT_TOKEN_BUDGET,
#      AGENT_TIMEOUT_S (wall clock for the agent loop, default 3600)
#      AGENT_BASE_SHA: the card's base commit when the bundle is a published branch head
#      (review runs need it; rework dev runs get it). It must be in the bundle and an ancestor
#      of the head; the prompt pack, the existing-file list and the changed files come from it.
#
# AGENT_MODE=agent runs the Claude Agent SDK loop (agent_runner.py): the task comes from the
# coordinator's get_task, the system prompt from AGENTS.md + docs/standards/ at the base commit.
# AGENT_MODE=stub makes no edits and only runs the repo's tests (isolation plumbing test).
# AGENT_MODE=plan is the read-only dev-lead planner: same checkout and base-commit prompt pack
# and the same env as agent mode, but no Postgres, no test runs and no result bundle. The agent
# leaves /out/plan.json (through its submit_plan tool) and the entrypoint writes
# /out/handoff.json (done exactly when plan.json is valid), plus agent.log and steps.jsonl.
# AGENT_MODE=triage is the read-only triager: plan mode's setup plus the base file list (the
# agent may run EXISTING tests, named one by one, to reproduce; still no Postgres, no suite
# run, no bundle). It leaves /out/triage.json (submit_triage) and the entrypoint writes
# /out/handoff.json (done exactly when triage.json is valid).
# AGENT_MODE=review is the read-only reviewer of a published branch: plan mode's setup at the
# branch head, plus the files the branch changed (git diff --name-only AGENT_BASE_SHA..HEAD,
# computed here before the agent starts). It leaves /out/review.json (submit_review) and the
# entrypoint writes /out/handoff.json (done exactly when review.json is valid).
# Then the repo tests are re-run here (agent mode: only if the agent changed something;
# otherwise skipped_checks says "no changes; tests not run"), whatever changed is committed on
# the branch, and result.bundle + handoff.json are written. Exit 0 when the plumbing worked
# (outcomes live in handoff.json, including early stops); non-zero only when the plumbing
# itself failed.
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
mkdir -p "$HOME" "$RESULTS" /tmp/vite-cache /tmp/vite-temp /tmp/uv-cache

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

# The card's base when the workspace starts at a published branch head (review, rework).
pack_sha="$base_sha"
if [[ -n "${AGENT_BASE_SHA:-}" ]]; then
  [[ "$AGENT_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail_handoff "AGENT_BASE_SHA must be a full commit id"
  git cat-file -e "${AGENT_BASE_SHA}^{commit}" 2>/dev/null || fail_handoff "AGENT_BASE_SHA is not in the bundle"
  git merge-base --is-ancestor "$AGENT_BASE_SHA" HEAD 2>/dev/null \
    || fail_handoff "AGENT_BASE_SHA is not an ancestor of the branch head"
  pack_sha="$AGENT_BASE_SHA"
  log "card base $(git rev-parse --short "$pack_sha")"
fi

# Baked dependencies: link, never install. The link is never committed.
printf '%s\n' '/web/node_modules' >> .git/info/exclude
if [[ -d web && ! -e web/node_modules ]]; then ln -s /opt/deps/web/node_modules web/node_modules; fi

AGENT_MODE="${AGENT_MODE:-agent}"
AGENT_DIR=/tmp/agent

need_agent_env() {
  for name in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN COORDINATOR_URL COORDINATOR_TOKEN RUN_ID; do
    [[ -n "${!name:-}" ]] || fail_handoff "AGENT_MODE=$AGENT_MODE needs $name"
  done
  timeout_s="${AGENT_TIMEOUT_S:-3600}"
  [[ "$timeout_s" =~ ^[0-9]{2,5}$ ]] || fail_handoff "AGENT_TIMEOUT_S must be 10-99999 seconds"
  # The prompt pack comes from the BASE commit, extracted before the agent can touch anything.
  mkdir -p "$AGENT_DIR/base"
  git archive --format=tar "$pack_sha" -- AGENTS.md docs/standards 2>/dev/null \
    | tar -x -C "$AGENT_DIR/base" 2>/dev/null \
    || fail_handoff "AGENTS.md or docs/standards/ missing at the base commit"
}

run_agent() { # extra runner args...; sets agent_rc
  # timeout signals the whole process group, so the CLI child dies with the runner.
  agent_rc=0
  timeout --kill-after=20 "$timeout_s" /opt/agent/venv/bin/python /opt/sandbox/agent_runner.py \
    --repo "$WORK" --base-dir "$AGENT_DIR/base" --out "$AGENT_DIR/result.json" --log-dir "$OUT" \
    "$@" || agent_rc=$?
  case $agent_rc in
    0) ;;
    124|137) log "agent: wall-clock timeout"; SKIPPED+=("agent: stopped at AGENT_TIMEOUT_S") ;;
    3) log "agent: setup failed (see above)" ;;
    4) log "agent: coordinator unreachable (see above); the model was not called" ;;
    *) log "agent: runner exited $agent_rc" ;;
  esac
  if [[ $agent_rc -ne 0 && -s "$AGENT_DIR/cli.log" ]]; then
    log "agent: last CLI stderr lines:"; tail -n 15 "$AGENT_DIR/cli.log" >&2 || true
  fi
}

# ------------------------------------------------------------ plan mode: read-only planner
if [[ "$AGENT_MODE" == plan ]]; then
  need_agent_env
  rm -f "$OUT/plan.json" "$OUT/plan.json.tmp" "$OUT/result.bundle"
  log "agent: dev-lead planner, read-only (timeout ${timeout_s}s)"
  run_agent --mode plan --plan-out "$OUT/plan.json"
  cd "$WORK"
  skip_args=()
  for s in "${SKIPPED[@]+"${SKIPPED[@]}"}"; do skip_args+=(--skipped "$s"); done
  /usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --plan-mode \
    --plan-json "$OUT/plan.json" --agent-result "$AGENT_DIR/result.json" \
    "${skip_args[@]+"${skip_args[@]}"}" --out "$OUT/handoff.json" || fail_handoff "handoff writer failed"
  if [[ -f "$OUT/plan.json" ]]; then log "wrote /out/plan.json and /out/handoff.json"
  else log "no plan written; wrote /out/handoff.json"; fi
  exit 0
fi

# ------------------------------------------------------------ triage mode: read-only triager
if [[ "$AGENT_MODE" == triage ]]; then
  need_agent_env
  rm -f "$OUT/triage.json" "$OUT/triage.json.tmp" "$OUT/result.bundle"
  # Existing tests at the base commit: the only files the triager may run.
  git ls-files -z > "$AGENT_DIR/base-files" || fail_handoff "git ls-files failed"
  log "agent: triager, read-only (timeout ${timeout_s}s)"
  run_agent --mode triage --triage-out "$OUT/triage.json" --base-files "$AGENT_DIR/base-files"
  cd "$WORK"
  skip_args=()
  for s in "${SKIPPED[@]+"${SKIPPED[@]}"}"; do skip_args+=(--skipped "$s"); done
  /usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --triage-mode \
    --triage-json "$OUT/triage.json" --agent-result "$AGENT_DIR/result.json" \
    "${skip_args[@]+"${skip_args[@]}"}" --out "$OUT/handoff.json" || fail_handoff "handoff writer failed"
  if [[ -f "$OUT/triage.json" ]]; then log "wrote /out/triage.json and /out/handoff.json"
  else log "no triage written; wrote /out/handoff.json"; fi
  exit 0
fi

# ------------------------------------------------------------ review mode: read-only reviewer
if [[ "$AGENT_MODE" == review ]]; then
  need_agent_env
  [[ -n "${AGENT_BASE_SHA:-}" ]] || fail_handoff "AGENT_MODE=review needs AGENT_BASE_SHA"
  rm -f "$OUT/review.json" "$OUT/review.json.tmp" "$OUT/result.bundle"
  # The files the branch changed: the only paths a finding may name.
  git diff --name-only -z "$pack_sha..HEAD" > "$AGENT_DIR/changed-files" || fail_handoff "git diff failed"
  log "agent: reviewer, read-only (timeout ${timeout_s}s)"
  run_agent --mode review --review-out "$OUT/review.json" --changed-files "$AGENT_DIR/changed-files"
  cd "$WORK"
  skip_args=()
  for s in "${SKIPPED[@]+"${SKIPPED[@]}"}"; do skip_args+=(--skipped "$s"); done
  /usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --review-mode \
    --review-json "$OUT/review.json" --changed-files "$AGENT_DIR/changed-files" \
    --agent-result "$AGENT_DIR/result.json" \
    "${skip_args[@]+"${skip_args[@]}"}" --out "$OUT/handoff.json" || fail_handoff "handoff writer failed"
  if [[ -f "$OUT/review.json" ]]; then log "wrote /out/review.json and /out/handoff.json"
  else log "no review written; wrote /out/handoff.json"; fi
  exit 0
fi

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
agent_args=()
RUN_TESTS=1
case "$AGENT_MODE" in
  stub) log "agent: stub (no edits)" ;;
  agent)
    need_agent_env
    # Existing files at the base commit (the card's base for a rework run): the runner refuses
    # edits to existing tests unless the card's modify-tests: label covers them.
    git ls-tree -r -z --name-only "$pack_sha" > "$AGENT_DIR/base-files" || fail_handoff "git ls-tree failed"
    # Offline caches for scripts/gen-api.sh (allowed while the run leases api/openapi.yaml).
    /opt/sandbox/offline-caches.sh || fail_handoff "offline generator caches failed"
    log "agent: claude agent sdk (timeout ${timeout_s}s)"
    run_agent --base-files "$AGENT_DIR/base-files"
    cd "$WORK"
    agent_args=(--agent-mode --agent-result "$AGENT_DIR/result.json")
    # No edits (committed or not) -> nothing to test; say so instead of re-running the suite.
    if [[ -z "$(git status --porcelain 2>/dev/null)" && "$(git rev-parse HEAD 2>/dev/null)" == "$base_sha" ]]; then
      RUN_TESTS=0
      SKIPPED+=("no changes; tests not run")
      agent_args+=(--tests-not-run)
      log "agent: no changes; tests not run"
    fi
    ;;
  *) fail_handoff "unknown AGENT_MODE (agent|plan|triage|review|stub)" ;;
esac

# ------------------------------------------------------------ 4. tests (JSON reports only; logs stay in /tmp)
report_args=()
if [[ $RUN_TESTS -eq 1 && ",$SUITES," == *",backend,"* ]]; then
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
if [[ $RUN_TESTS -eq 1 && ",$SUITES," == *",web,"* ]]; then
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
git diff --name-only "$pack_sha..HEAD" > /tmp/changed-paths.txt
/usr/local/bin/python3 /opt/sandbox/handoff.py --task-id "$TASK_ID" --changed /tmp/changed-paths.txt \
  "${report_args[@]+"${report_args[@]}"}" "${skip_args[@]+"${skip_args[@]}"}" \
  "${agent_args[@]+"${agent_args[@]}"}" --out "$OUT/handoff.json" || fail_handoff "handoff writer failed"
log "wrote /out/result.bundle and /out/handoff.json"
