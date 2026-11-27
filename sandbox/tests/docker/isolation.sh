#!/usr/bin/env bash
# Prove the local sandbox isolation with real containers.
#
#   sandbox/tests/docker/isolation.sh [--build] [--snapshot]
#
#   --build     rebuild jobradar-sandbox-writer:dev and jobradar-llm-proxy:dev first
#               (they are built automatically when missing)
#   --snapshot  also run the stub agent (AGENT_MODE=stub) on a snapshot of the current working
#               tree, so the repo's backend and web tests run inside the sandbox (slow)
#
# Checks (each prints PASS/FAIL):
#   1. egress: a container on jobradar_sandbox can't reach example.com or 169.254.169.254,
#      directly or through the egress proxy, and private ranges are refused; it CAN reach the
#      coordinator (:8100) directly and through the proxy, and nothing else on those hosts
#      (the compose coordinator if it is running, else a throwaway stand-in);
#   2. secrets: no ANTHROPIC_API_KEY / gcloud credentials / ~/.config/jobradar in env or on disk;
#   3. the root filesystem is read-only (only /workspace and /tmp are writable), non-root user;
#   4. llm-proxy refuses missing, expired, forged and over-budget tokens;
#   5. plumbing: bundle in -> result.bundle + handoff.json out (AGENT_MODE=stub);
#   6. agent mode fails closed: with fake tokens and no working coordinator the run ends with
#      a "failed" handoff and no edits (proves the image's agent runner and its wiring).
# Exit: 0 all passed, 1 a check failed, 77 skipped (Docker not available).
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
WRITER_IMAGE="jobradar-sandbox-writer:dev"
PROXY_IMAGE="jobradar-llm-proxy:dev"
TEST_PROXY="jr-test-llm-proxy"
TEST_COORD="jr-test-coordinator"
BUILD=0 SNAPSHOT=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --snapshot) SNAPSHOT=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '[isolation] %s\n' "$*" >&2; }
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  say "SKIP: Docker is not available"; exit 77
fi
pick_py() {
  local c
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
      echo "$c"; return
    fi
  done
  return 1
}
PY="$(pick_py)" || { say "SKIP: no python >= 3.9 on the host (needed to mint test tokens)"; exit 77; }
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }

# A native mixed path (C:/...) on Windows: MSYS_NO_PATHCONV stops Git Bash translating /tmp,
# so Windows git and docker would otherwise get a path they can't resolve.
WORK="$(mktemp -d)"
if command -v cygpath >/dev/null 2>&1; then WORK="$(cygpath -m "$WORK")"; fi
cleanup() {
  docker rm -f "$TEST_PROXY" "$TEST_COORD" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
FAILS=0
check() { # name, command...
  local name="$1"; shift
  if "$@"; then say "PASS $name"; else say "FAIL $name"; FAILS=$((FAILS + 1)); fi
}

# ------------------------------------------------------------ images, network, egress proxy
cd "$ROOT"
# Full build output goes to a log (quiet builds hid errors); its tail is shown on failure.
BUILD_LOG="$ROOT/.artifacts/isolation-build.log"
mkdir -p "$ROOT/.artifacts"
build() { # docker build args...
  if ! docker build --progress=plain "$@" >>"$BUILD_LOG" 2>&1; then
    say "docker build failed; last lines of .artifacts/isolation-build.log:"
    tail -n 25 "$BUILD_LOG" >&2
    exit 1
  fi
}
: > "$BUILD_LOG"
if [[ $BUILD -eq 1 ]] || ! docker image inspect "$WRITER_IMAGE" >/dev/null 2>&1; then
  say "building $WRITER_IMAGE (first build takes several minutes)"
  build -f sandbox/writer/Dockerfile -t "$WRITER_IMAGE" .
fi
if [[ $BUILD -eq 1 ]] || ! docker image inspect "$PROXY_IMAGE" >/dev/null 2>&1; then
  build -t "$PROXY_IMAGE" sandbox/llm-proxy
fi
# Vertex mode is the more permissive endpoint config, so test against it.
AGENT_MODEL_ENDPOINT="${AGENT_MODEL_ENDPOINT:-vertex}" \
  docker compose -f docker-compose.yml up -d --wait egress-proxy >/dev/null 2>&1
# A running squid keeps the config it started with; make it re-read the mounted files.
docker compose -f docker-compose.yml exec -T egress-proxy squid -k reconfigure >/dev/null 2>&1
sleep 2
check "sandbox network is internal" \
  test "$(docker network inspect -f '{{.Internal}}' jobradar_sandbox)" = true

# The coordinator: the compose service when it is up on the sandbox network, otherwise a
# throwaway stand-in (a static HTTP server: no /v1 API) under the same network alias and port.
real_coord="$(docker ps -q --filter label=com.docker.compose.service=coordinator \
  --filter network=jobradar_sandbox 2>/dev/null || true)"
docker rm -f "$TEST_COORD" >/dev/null 2>&1 || true
if [[ -z "$real_coord" ]]; then
  docker run -d --name "$TEST_COORD" --network jobradar_sandbox --network-alias coordinator \
    --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
    --entrypoint /usr/local/bin/python3 "$WRITER_IMAGE" \
    -m http.server 8100 --bind 0.0.0.0 --directory /tmp >/dev/null
  say "coordinator: throwaway stand-in (compose coordinator not running)"
else
  say "coordinator: compose service"
fi

# A throwaway llm-proxy with a random secret, a fake key and a dead upstream. Values are only
# ever passed by env NAME, so they never appear in process listings.
LLM_PROXY_SECRET="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')"
ANTHROPIC_API_KEY="fake-isolation-test-key-$("$PY" -c 'import secrets; print(secrets.token_hex(8))')"
export LLM_PROXY_SECRET ANTHROPIC_API_KEY
docker rm -f "$TEST_PROXY" >/dev/null 2>&1 || true
docker run -d --name "$TEST_PROXY" --network jobradar_sandbox --read-only --cap-drop ALL \
  --security-opt no-new-privileges -e LLM_PROXY_SECRET -e ANTHROPIC_API_KEY \
  -e LLM_PROXY_UPSTREAM=http://127.0.0.1:9 "$PROXY_IMAGE" >/dev/null
for _ in $(seq 1 30); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$TEST_PROXY" 2>/dev/null)" == healthy ]] && break
  sleep 1
done
mint() { PYTHONPATH="$(host_path "$ROOT/sandbox/llm-proxy")" "$PY" -m llm_proxy.mint "$@"; }
PROBE_TOKEN_VALID="$(mint --task-id iso-1 --ttl 600 --budget 100000)"
PROBE_TOKEN_SMALL="$(mint --task-id iso-2 --ttl 600 --budget 10)"
PROBE_TOKEN_EXPIRED="$(mint --task-id iso-3 --ttl -60 --budget 100000)"
PROBE_TOKEN_FORGED="$(LLM_PROXY_SECRET="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')" \
  mint --task-id iso-4 --ttl 600 --budget 100000)"
export PROBE_TOKEN_VALID PROBE_TOKEN_SMALL PROBE_TOKEN_EXPIRED PROBE_TOKEN_FORGED
unset ANTHROPIC_API_KEY LLM_PROXY_SECRET  # never forwarded to the sandbox (run.sh passes names it knows)

# ------------------------------------------------------------ probes in the hardened container
mkdir -p "$WORK/in" "$WORK/out" "$WORK/repo"
git -C "$WORK/repo" -c init.defaultBranch=main init -q
printf 'isolation test\n' > "$WORK/repo/README.md"
git -C "$WORK/repo" add README.md
git -C "$WORK/repo" -c user.name=t -c user.email=t@example.test commit -qm base
git -C "$WORK/repo" bundle create "$WORK/in/input.bundle" HEAD main >/dev/null 2>&1

probe() { # extra args for the probe
  "$ROOT/sandbox/run.sh" --task-id "probe-$RANDOM" --in "$WORK/in" --out "$WORK/out" \
    --entrypoint /usr/local/bin/python3 --arg /probe/probe.py "$@" \
    -- --mount "type=bind,source=$(host_path "$HERE"),target=/probe,readonly" \
       -e PROBE_LLM_PROXY="$TEST_PROXY:8080" \
       -e PROBE_TOKEN_VALID -e PROBE_TOKEN_SMALL -e PROBE_TOKEN_EXPIRED -e PROBE_TOKEN_FORGED
}
show() { # print the JSON result one check per line
  "$PY" -c 'import json,sys; [print(f"  {k}: {v}") for k, v in json.load(sys.stdin).items()]' >&2
}
# The probe reads the PROBE_TOKEN_* names, which are tokens for a throwaway proxy, not secrets.
if out="$(probe 2>/dev/null)"; then rc=0; else rc=$?; fi
printf '%s' "$out" | show || true
check "egress, secrets and filesystem probe" test "$rc" -eq 0
if out="$(probe --arg tokens 2>/dev/null)"; then rc=0; else rc=$?; fi
printf '%s' "$out" | show || true
check "llm-proxy token refusals" test "$rc" -eq 0

# ------------------------------------------------------------ plumbing: bundle in, bundle out
plumbing() { # input dir, label
  local in="$1" out="$WORK/out-$2"
  mkdir -p "$out"
  AGENT_MODE=stub "$ROOT/sandbox/run.sh" --task-id "iso-$2" --in "$in" --out "$out" \
    >"$WORK/run-$2.log" 2>&1 || return 1
  git bundle verify -q "$out/result.bundle" >/dev/null 2>&1 || return 1
  "$PY" - "$out/handoff.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1], encoding="utf-8"))
need = {"status", "summary", "changed_paths", "tests", "rubric_checked", "open_questions", "skipped_checks"}
assert need <= set(h), sorted(need - set(h))
assert h["status"] in ("done", "failed"), h["status"]
t = h["tests"]
print(f"  status={h['status']} passed={t['passed']} failed={t['failed']} first_failing={t['first_failing']}", file=sys.stderr)
print(f"  skipped_checks={h['skipped_checks']}", file=sys.stderr)
PY
}
SANDBOX_POSTGRES=0 check "plumbing (synthetic repo)" plumbing "$WORK/in" synthetic

# ------------------------------------------------------------ agent mode fails closed
agent_fails_closed() {
  local repo="$WORK/repo-agent" in="$WORK/in-agent" out="$WORK/out-agent"
  mkdir -p "$repo/docs/standards" "$in" "$out"
  printf '# AGENTS (isolation test)\n' > "$repo/AGENTS.md"
  printf '# testing (isolation test)\n' > "$repo/docs/standards/testing.md"
  git -C "$repo" -c init.defaultBranch=main init -q
  git -C "$repo" add -A
  git -C "$repo" -c user.name=t -c user.email=t@example.test commit -qm base
  git -C "$repo" bundle create "$in/input.bundle" HEAD main >/dev/null 2>&1
  # Fake, unsigned tokens: the stand-in has no /v1 API, the real coordinator refuses them.
  AGENT_MODE=agent SANDBOX_POSTGRES=0 RUN_ID=iso-run-1 AGENT_TIMEOUT_S=120 \
    ANTHROPIC_AUTH_TOKEN=jrt1.isolation.fake COORDINATOR_TOKEN=jrc1.isolation.fake \
    "$ROOT/sandbox/run.sh" --task-id iso-agent --in "$in" --out "$out" \
    >"$WORK/run-agent.log" 2>&1 || return 1
  grep -q "setup failed" "$WORK/run-agent.log" || return 1
  "$PY" - "$out/handoff.json" <<'PY'
import json, sys
h = json.load(open(sys.argv[1], encoding="utf-8"))
assert h["status"] == "failed", h["status"]
assert h["changed_paths"] == [], h["changed_paths"]
assert "without post_handoff" in h["summary"], h["summary"]
print(f"  status={h['status']} summary={h['summary'][:70]}...", file=sys.stderr)
PY
}
check "agent mode fails closed without a coordinator task" agent_fails_closed

if [[ $SNAPSHOT -eq 1 ]]; then
  # Snapshot of tracked + untracked-but-not-ignored files; the project's own git is only read.
  snap="$WORK/snap"; mkdir -p "$snap" "$WORK/in-snap"
  git -C "$ROOT" ls-files -z -co --exclude-standard | (cd "$ROOT" && xargs -0 tar -cf - --) | tar -xf - -C "$snap"
  git -C "$snap" -c init.defaultBranch=main init -q
  git -C "$snap" add -A
  git -C "$snap" -c user.name=t -c user.email=t@example.test commit -qm snapshot
  git -C "$snap" bundle create "$WORK/in-snap/input.bundle" HEAD main >/dev/null 2>&1
  check "plumbing (working-tree snapshot, real tests)" plumbing "$WORK/in-snap" snapshot
fi

if [[ $FAILS -gt 0 ]]; then say "$FAILS check(s) FAILED"; exit 1; fi
say "all checks passed"
