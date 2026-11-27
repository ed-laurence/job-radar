#!/usr/bin/env bash
# The single quality gate. CI calls exactly this script; run it locally before reporting done.
#
#   scripts/check.sh                 every step
#   scripts/check.sh --fast          skip slow steps (integration tests, tofu validate/test, Checkov,
#                                    Conftest, Semgrep, dependency audits, the security agent)
#   scripts/check.sh --security      only the security steps (the local twin of security-review.yml)
#   scripts/check.sh --only a,b      only the named step ids (see --list)
#   scripts/check.sh --list          print step ids
#
# Every step reports PASS, FAIL or SKIP(reason). A SKIP is never counted as a PASS.
# Exit code: 1 if any step FAILs, else 0. Logs: .artifacts/check/<step>.log
# Env: DATABASE_URL (enables integration tests), JOBRADAR_DENYLIST (denylist file path),
#      CHECK_BASE_REF (base for oasdiff / the security agent; default origin/main),
#      SECURITY_AGENT=1 (run the security-auditor agent through the `claude` CLI).
set -euo pipefail
JR_SCRIPT=check
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

FAST=0 SECURITY=0 ONLY="" LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fast) FAST=1 ;;
    --security) SECURITY=1 ;;
    --only) ONLY="${2:?--only needs a comma-separated list}"; shift ;;
    --only=*) ONLY="${1#--only=}" ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

cd "$JR_ROOT"
LOG_DIR="$JR_ARTIFACTS/check"
mkdir -p "$LOG_DIR"
SKIP_FILE="$LOG_DIR/.skip-reason"
BASE_REF="${CHECK_BASE_REF:-origin/main}"

# ------------------------------------------------------------------ helpers
skip() { printf '%s' "$*" > "$SKIP_FILE"; return 77; }
need() { have "$1" || skip "${2:-$1} not installed"; }
need_backend() { [[ -f backend/pyproject.toml ]] || skip "backend/ does not exist yet"; need uv; }
need_web() {
  [[ -f web/package.json ]] || skip "web/ does not exist yet"
  pnpm_cmd >/dev/null || skip "pnpm not installed (no corepack or npx either)"
  if [[ ! -d web/node_modules ]]; then (cd web && run_pnpm install --frozen-lockfile); fi
}
need_infra() {
  [[ -d infra ]] || skip "infra/ does not exist yet"
  [[ -n "$(find infra -name '*.tf' -not -path '*/.terraform/*' | head -1)" ]] || skip "infra/ has no .tf files yet"
  IAC="$(iac_cmd)" || skip "tofu not installed (and no terraform fallback)"
}
# pytest exit 5 = no tests collected: that is a SKIP, not a PASS.
pytest_rc() { local rc=0; "$@" || rc=$?; [[ $rc -eq 5 ]] && skip "no tests collected"; return $rc; }

# init without a backend; never rewrite a committed lockfile. Providers come from one shared
# cache (~/.cache/jobradar/tf-plugin-cache) instead of a fresh download per directory.
iac_init() {
  local lock=(); [[ -f "$1/.terraform.lock.hcl" ]] && lock=(-lockfile=readonly)
  mkdir -p "$JR_CACHE_DIR/tf-plugin-cache"
  TF_PLUGIN_CACHE_DIR="$(native_path "$JR_CACHE_DIR/tf-plugin-cache")" \
    "$IAC" -chdir="$1" init -backend=false -input=false -no-color "${lock[@]}" >/dev/null
}
# Root modules: directories under infra/ with .tf files, except infra/modules/** (child modules
# are validated through the roots that call them and through infra/tests).
iac_roots() {
  find infra -name '*.tf' -not -path '*/.terraform/*' -not -path 'infra/modules/*' -exec dirname {} \; | sort -u
}

# ------------------------------------------------------------------ steps
# Each step: 0 = PASS, 77 = SKIP (reason via skip), anything else = FAIL.

step_spectral() {
  [[ -f api/openapi.yaml ]] || skip "api/openapi.yaml does not exist yet"
  need npx "npx (Node)"
  local ruleset=""
  for r in api/.spectral.yaml .spectral.yaml api/.spectral.yml .spectral.yml; do [[ -f $r ]] && { ruleset=$r; break; }; done
  if [[ -z $ruleset ]]; then
    ruleset="$LOG_DIR/spectral-default.yaml"; printf 'extends: ["spectral:oas"]\n' > "$ruleset"
    echo "no .spectral.yaml found; using spectral:oas"
  fi
  npx -y "@stoplight/spectral-cli@${JR_SPECTRAL_VERSION}" lint api/openapi.yaml --ruleset "$ruleset" --fail-severity warn
}

step_oasdiff() {
  [[ -f api/openapi.yaml ]] || skip "api/openapi.yaml does not exist yet"
  need oasdiff
  git rev-parse --verify -q "$BASE_REF" >/dev/null || skip "base ref $BASE_REF not found (no commits yet?)"
  git cat-file -e "$BASE_REF:api/openapi.yaml" 2>/dev/null || skip "api/openapi.yaml not in $BASE_REF"
  local base="$LOG_DIR/openapi.base.yaml"
  git show "$BASE_REF:api/openapi.yaml" > "$base"
  oasdiff breaking "$base" api/openapi.yaml --fail-on ERR
}

step_gen_api() {
  local rc=0
  scripts/gen-api.sh --check || rc=$?
  [[ $rc -eq 3 ]] && skip "no spec, or no backend/ and web/ yet"
  return $rc
}

step_ruff()        { need_backend; uv_backend ruff check --output-format concise .; }
step_ruff_format() { need_backend; uv_backend ruff format --check .; }
step_pyright()     { need_backend; uv_backend pyright; }
step_imports() {
  need_backend
  [[ -f backend/.importlinter ]] || grep -q '^\[tool\.importlinter\]' backend/pyproject.toml \
    || { echo "no import-linter contracts (backend/.importlinter or [tool.importlinter]); layering is unchecked"; return 1; }
  uv_backend lint-imports
}
step_pytest() {
  need_backend
  pytest_rc uv_backend pytest -q --tb=short -m "not integration and not evals" -p no:cacheprovider
}
step_pytest_integration() {
  need_backend
  [[ -n "${DATABASE_URL:-}" ]] || skip "DATABASE_URL not set (start one: eval \"export \$(scripts/dev-db.sh)\")"
  pytest_rc uv_backend pytest -q --tb=short -m integration -p no:cacheprovider
}

step_biome() { need_web; (cd web && run_pnpm exec biome ci .); }
step_tsc()   { need_web; (cd web && run_pnpm exec tsc --noEmit); }
step_depcruise() {
  need_web
  compgen -G "web/.dependency-cruiser.*" >/dev/null \
    || { echo "no web/.dependency-cruiser.* config; layering is unchecked"; return 1; }
  (cd web && run_pnpm exec depcruise src)
}
step_vitest() {
  need_web
  local out="$LOG_DIR/vitest.out" rc=0
  (cd web && run_pnpm exec vitest run) 2>&1 | tee "$out" || rc=$?
  [[ $rc -ne 0 ]] && grep -q 'No test files found' "$out" && skip "no test files found"
  return $rc
}

iac_label() { [[ "${IAC:-}" == terraform ]] && echo "terraform fallback: OpenTofu-only features (state encryption) may not validate"; return 0; }
step_tofu_fmt() {
  need_infra; iac_label
  "$IAC" fmt -check -recursive -diff infra
}
step_tofu_validate() {
  need_infra; iac_label
  local d rc=0 roots
  roots="$(iac_roots)"; [[ -n $roots ]] || skip "no .tf directories under infra/"
  for d in $roots; do
    echo "== $d"
    iac_init "$d" && "$IAC" -chdir="$d" validate -no-color || rc=1
  done
  return $rc
}
step_tofu_test() {
  need_infra; iac_label
  local d rc=0 ran=0 roots
  # A tests/ dir without its own .tf files belongs to its parent module (tofu's default layout);
  # infra/tests has a main.tf and is its own harness root.
  roots="$(find infra -name '*.tftest.hcl' -not -path '*/.terraform/*' -exec dirname {} \; | sort -u \
    | while read -r d; do
        if [[ "$(basename "$d")" == tests ]] && ! compgen -G "$d/*.tf" >/dev/null; then dirname "$d"; else echo "$d"; fi
      done | sort -u)"
  for d in $roots; do
    echo "== $d"; ran=1
    iac_init "$d" && "$IAC" -chdir="$d" test -no-color || rc=1
  done
  [[ $ran -eq 1 ]] || skip "no *.tftest.hcl files yet"
  return $rc
}
step_checkov() {
  [[ -d infra ]] || skip "infra/ does not exist yet"
  need checkov
  checkov -d infra --framework terraform --quiet --compact --skip-path .terraform
}
# Policies target `tofu show -json` plan output, which needs credentials (deploy.yml runs that).
# Locally: the policies' own unit tests, plus any plan JSON you saved at .artifacts/plan.json.
step_conftest() {
  [[ -d infra/policy ]] || skip "infra/policy/ does not exist yet"
  compgen -G "infra/policy/*.rego" >/dev/null || skip "no .rego policies yet"
  need conftest
  conftest verify --policy infra/policy
  if [[ -f "$JR_ARTIFACTS/plan.json" ]]; then conftest test --policy infra/policy "$JR_ARTIFACTS/plan.json"; fi
}

step_gitleaks() {
  need gitleaks
  local cfg=(); [[ -f .gitleaks.toml ]] && cfg=(--config .gitleaks.toml)
  if gitleaks dir --help >/dev/null 2>&1; then
    gitleaks dir . "${cfg[@]}" --redact --no-banner --exit-code 1
  else
    gitleaks detect --no-git --source . "${cfg[@]}" --redact --no-banner --exit-code 1
  fi
}

# Denylisted identifiers (project id, emails, domain, bucket names...). Values are never printed.
step_denylist() {
  local list="${JOBRADAR_DENYLIST:-$JR_CONFIG_DIR/denylist.txt}"
  [[ -f "$list" ]] || skip "no denylist at $list"
  local dirs=()
  for built in web/.next-build web/.next; do [[ -d "$built" ]] && { echo "also scanning $built (built bundle)"; dirs+=(--dir "$built"); }; done
  git ls-files -z -co --exclude-standard     | run_py "$(native_path "$JR_ROOT/scripts/lib/denylist_scan.py")" "$(native_path "$list")" "${dirs[@]}"
}

step_seed_pii() {
  [[ -f scripts/build_seed.py ]] || skip "scripts/build_seed.py does not exist yet"
  need uv
  if grep -q '^# /// script' scripts/build_seed.py; then
    uv run --quiet scripts/build_seed.py --verify
  else
    uv run --no-project --quiet python scripts/build_seed.py --verify
  fi
  # The demo CV PDFs carry only their gate-checked JSON text: they must match a fresh render.
  [[ -f scripts/build_demo_cvs.py ]] && uv run --quiet scripts/build_demo_cvs.py --check
}

# Sandbox helpers (llm-proxy, writer handoff): stdlib code with its own pinned tools.
step_sandbox() {
  [[ -f sandbox/pyproject.toml ]] || skip "sandbox/ does not exist yet"
  need uv
  (cd sandbox     && uv run --no-project --quiet --with pytest==8.4.2 pytest -q --tb=short     && uv run --no-project --quiet --with ruff==0.17.4 ruff check .     && uv run --no-project --quiet --with ruff==0.17.4 ruff format --check .     && uv run --no-project --quiet --with pyright==1.1.414 --with pytest==8.4.2 --with claude-agent-sdk==0.2.189 pyright)
}

# Real-container isolation proofs (egress, no secrets, read-only root, proxy tokens).
step_sandbox_isolation() {
  [[ -x sandbox/tests/docker/isolation.sh || -f sandbox/tests/docker/isolation.sh ]] || skip "no isolation tests yet"
  docker_ok || skip "Docker not running"
  local rc=0
  bash sandbox/tests/docker/isolation.sh || rc=$?
  [[ $rc -eq 77 ]] && skip "isolation.sh: Docker not available"
  return "$rc"
}

# Agent files: exactly the sections Role, Scope, Process, Output, References, in that order.
step_agent_lint() {
  compgen -G ".claude/agents/*.md" >/dev/null || skip "no .claude/agents/*.md"
  local f rc=0 got want name
  want=$'Role\nScope\nProcess\nOutput\nReferences'
  for f in .claude/agents/*.md; do
    got="$(sed 's/\r$//' "$f" | awk '/^```/{fence=!fence} !fence && /^#{1,6} /' | sed -E 's/^## +//; s/[[:space:]]+$//')"
    if [[ "$got" != "$want" ]]; then
      echo "$f: headings must be exactly '## Role/Scope/Process/Output/References' (got: $(echo "$got" | tr '\n' '|'))"; rc=1
    fi
    name="$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1 | tr -d '\r"')"
    if [[ "$name" != "$(basename "$f" .md)" ]]; then
      echo "$f: frontmatter name '$name' must match the file name"; rc=1
    fi
    if ! sed 's/\r$//' "$f" | awk '/^## References/{r=1;next} /^## /{r=0} r' | grep -q 'docs/standards'; then
      echo "$f: References must point to the standards pack (docs/standards/)"; rc=1
    fi
  done
  [[ $rc -eq 0 ]] && echo "$(ls .claude/agents/*.md | wc -l) agent files OK"
  return $rc
}

step_semgrep() {
  need semgrep
  local cfg=(--config p/owasp-top-ten)
  [[ -d .semgrep ]] && cfg+=(--config .semgrep)
  semgrep scan "${cfg[@]}" --error --metrics=off --quiet
}
step_pip_audit() {
  need_backend
  [[ -f backend/uv.lock ]] || skip "backend/uv.lock does not exist yet"
  local req="$LOG_DIR/requirements.audit.txt"
  (cd backend && uv export --frozen --no-dev --no-emit-project --format requirements-txt) > "$req"
  uv run --no-project --quiet --with pip-audit pip-audit --strict --disable-pip --no-deps -r "$(native_path "$req")"
}
step_pnpm_audit() {
  need_web
  (cd web && run_pnpm audit --prod --audit-level high)
}
step_security_agent() {
  [[ "${SECURITY_AGENT:-0}" == 1 ]] || skip "opt-in: set SECURITY_AGENT=1 (runs the security-auditor via the claude CLI)"
  need claude "claude CLI"
  git rev-parse --verify -q "$BASE_REF" >/dev/null || skip "base ref $BASE_REF not found (no commits yet?)"
  local dir="$LOG_DIR/security-agent"; rm -rf "$dir"; mkdir -p "$dir"
  git diff --no-ext-diff "$BASE_REF"...HEAD > "$dir/diff.patch"
  git diff --no-ext-diff >> "$dir/diff.patch"
  [[ -s "$dir/diff.patch" ]] || skip "empty diff against $BASE_REF"
  scripts/lib/security-agent.sh "$dir"
}

# ------------------------------------------------------------------ registry
# id | tags | function. Tags: core, slow, sec.
STEPS=(
  "spectral|core|step_spectral"
  "oasdiff|core|step_oasdiff"
  "gen-api|core|step_gen_api"
  "ruff|core|step_ruff"
  "ruff-format|core|step_ruff_format"
  "pyright|core|step_pyright"
  "import-linter|core|step_imports"
  "pytest|core|step_pytest"
  "pytest-integration|slow|step_pytest_integration"
  "biome|core|step_biome"
  "tsc|core|step_tsc"
  "depcruise|core|step_depcruise"
  "vitest|core|step_vitest"
  "tofu-fmt|core|step_tofu_fmt"
  "tofu-validate|slow|step_tofu_validate"
  "tofu-test|slow|step_tofu_test"
  "checkov|sec slow|step_checkov"
  "conftest|sec slow|step_conftest"
  "gitleaks|sec|step_gitleaks"
  "denylist|sec|step_denylist"
  "seed-pii|sec|step_seed_pii"
  "agent-lint|core|step_agent_lint"
  "sandbox|core|step_sandbox"
  "sandbox-isolation|sec slow|step_sandbox_isolation"
  "semgrep|sec slow|step_semgrep"
  "pip-audit|sec slow|step_pip_audit"
  "pnpm-audit|sec slow|step_pnpm_audit"
  "security-agent|sec slow|step_security_agent"
)

if [[ $LIST -eq 1 ]]; then
  for s in "${STEPS[@]}"; do IFS='|' read -r id tags _ <<<"$s"; printf '%-20s %s\n' "$id" "$tags"; done
  exit 0
fi

if [[ -n $ONLY ]]; then
  for want in ${ONLY//,/ }; do
    found=0; for s in "${STEPS[@]}"; do [[ "${s%%|*}" == "$want" ]] && found=1; done
    [[ $found -eq 1 ]] || die "unknown step id: $want (see --list)"
  done
fi

results=() n_pass=0 n_fail=0 n_skip=0 failed_logs=()
record() { # id status detail
  results+=("$1|$2|$3")
  case "$2" in PASS) n_pass=$((n_pass+1)) ;; FAIL) n_fail=$((n_fail+1)) ;; SKIP) n_skip=$((n_skip+1)) ;; esac
  local color="$_c_grn"; [[ $2 == FAIL ]] && color="$_c_red"; [[ $2 == SKIP ]] && color="$_c_ylw"
  if [[ $2 == SKIP ]]; then printf '%s%-5s%s %-20s (%s)\n' "$color" "$2" "$_c_off" "$1" "$3"
  else printf '%s%-5s%s %-20s %s\n' "$color" "$2" "$_c_off" "$1" "$3"; fi
}

for s in "${STEPS[@]}"; do
  IFS='|' read -r id tags fn <<<"$s"
  if [[ -n $ONLY ]]; then
    [[ ",$ONLY," == *",$id,"* ]] || continue
  else
    if [[ $SECURITY -eq 1 && " $tags " != *" sec "* ]]; then record "$id" SKIP "--security: not a security step"; continue; fi
    if [[ $FAST -eq 1 && " $tags " == *" slow "* ]]; then record "$id" SKIP "--fast"; continue; fi
  fi
  log_file="$LOG_DIR/$id.log"
  rm -f "$SKIP_FILE"
  start=$SECONDS
  set +e
  ( set -euo pipefail; "$fn" ) > "$log_file" 2>&1
  rc=$?
  set -e
  dur="$((SECONDS - start))s"
  if [[ $rc -eq 0 ]]; then
    note=""; grep -q '^terraform fallback' "$log_file" && note=" [terraform fallback]"
    record "$id" PASS "$dur$note"
  elif [[ $rc -eq 77 && -f $SKIP_FILE ]]; then
    record "$id" SKIP "$(cat "$SKIP_FILE")"
  else
    note=""; grep -q '^terraform fallback' "$log_file" && note=" [terraform fallback]"
    record "$id" FAIL "$dur rc=$rc$note -> ${log_file#"$JR_ROOT"/}"
    failed_logs+=("$log_file")
  fi
done
rm -f "$SKIP_FILE"

for f in "${failed_logs[@]+"${failed_logs[@]}"}"; do
  printf '\n%s--- %s (last 15 lines)%s\n' "$_c_red" "${f#"$JR_ROOT"/}" "$_c_off"
  tail -n 15 "$f"
done

summary="check.sh: $n_pass passed, $n_fail failed, $n_skip skipped"
printf '\n%s\n' "$summary"
{
  printf '%s\n' "$summary"
  for r in "${results[@]+"${results[@]}"}"; do IFS='|' read -r id st det <<<"$r"; printf '%s\t%s\t%s\n' "$st" "$id" "$det"; done
} > "$LOG_DIR/summary.txt"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    printf '### %s\n\n| status | step | detail |\n|---|---|---|\n' "$summary"
    for r in "${results[@]+"${results[@]}"}"; do IFS='|' read -r id st det <<<"$r"; printf '| %s | %s | %s |\n' "$st" "$id" "$det"; done
  } >> "$GITHUB_STEP_SUMMARY"
fi

[[ $n_fail -eq 0 ]]
