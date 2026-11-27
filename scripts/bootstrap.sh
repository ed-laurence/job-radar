#!/usr/bin/env bash
# One-time GCP + GitHub bootstrap, run by the OWNER. Staged (ADR-0008).
# Idempotent and safe to re-run. THIS CREATES BILLABLE CLOUD RESOURCES.
#
#   scripts/bootstrap.sh --gcp-only --i-understand-this-creates-gcp-resources   (now: Vertex for local dev)
#   scripts/bootstrap.sh --i-understand-this-creates-gcp-resources              (at initial-commit time)
#   scripts/bootstrap.sh --finalize --i-understand-this-creates-gcp-resources   (after the first deploy)
#
# --gcp-only touches nothing on GitHub: no gh, no GitHub provider resources (enable_github = false),
# no deploy key, no webhook HMAC, no Actions secrets. It still creates the WIF providers, pinned to
# the future repo (GH_OWNER/GH_REPO + the owner's numeric id, read anonymously from the public
# GitHub API or typed in). The later full run adds the GitHub parts on top, idempotently. Once the
# GitHub parts exist, --gcp-only refuses to run (it would plan their removal).
#
# What it does (docs/plan.md "Repository, commits and the GCP bootstrap"):
#   1. Checks tools: tofu (OpenTofu >= 1.8, required: Terraform can't do state encryption), gcloud,
#      uv, pnpm, docker; gh and ssh-keygen only for the full run.
#   2. Interactive sign-ins, only when not already signed in: gcloud (+ ADC); gh (full run only).
#   3. Organization: uses one if it exists; otherwise walks you through Cloud Identity Free
#      (DNS TXT verification) and waits. If that fails it asks before continuing without VPC-SC.
#   4. Billing account pick; prompts once for the two account emails. Writes the UNTRACKED
#      ~/.config/jobradar/{bootstrap.tfvars,prod.tfvars,denylist.txt} (mode 600).
#   5. infra/bootstrap with OpenTofu: pass 1 local state (encryption off, key doesn't exist yet),
#      pass 2 migrates state to the GCS bucket with KMS client-side encryption.
#   6. Outside OpenTofu (no secrets in state): APP_USERS and the denylist to Secret Manager.
#      Full run only: deploy key (ssh-keygen) and webhook HMAC (openssl) straight to Secret
#      Manager and GitHub; PROD_TFVARS and JOBRADAR_DENYLIST to GitHub Actions secrets.
#   7. Vertex access checks with test calls: Claude (Model Garden enablement / terms), Gemini,
#      and the open-weight MaaS model when MAAS_MODEL is set.
#   8. Summary of what exists and what comes later (GCP-only: with a monthly cost estimate).
# --finalize: applies infra/github-finalize (rulesets) and registers the GitHub webhook.
#
# Env overrides: JR_REGION (default australia-southeast1), GEMINI_MODEL, MAAS_MODEL, MAAS_REGION,
#                NAME_PREFIX (default jr), WEBHOOK_PATH (default /hooks/github, on the public web service).
set -euo pipefail
JR_SCRIPT=bootstrap
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CONFIRM=0 FINALIZE=0 GCP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --i-understand-this-creates-gcp-resources) CONFIRM=1 ;;
    --finalize) FINALIZE=1 ;;
    --gcp-only) GCP_ONLY=1 ;;
    -h|--help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done
[[ $GCP_ONLY -eq 1 && $FINALIZE -eq 1 ]] && die "--gcp-only and --finalize can't be combined (--finalize needs GitHub)"

# OpenTofu is mandatory: state encryption (encryption.tofu) is OpenTofu-only; Terraform would
# ignore it and write plaintext state. No fallback. Read-only check, before anything else.
if ! have tofu; then
  {
    echo "Refusing to run: OpenTofu (tofu) is required and was not found on PATH."
    have terraform && echo "  terraform is installed, but it can't do client-side state encryption; it is NOT a fallback."
    echo "  Install OpenTofu >= 1.8, then open a new shell:"
    echo "    Windows:  winget install OpenTofu.Tofu"
    echo "    macOS:    brew install opentofu"
    echo "    Linux:    https://opentofu.org/docs/intro/install/"
  } >&2
  exit 1
fi
tofu_ver="$(tofu version 2>/dev/null | head -1 | sed -n 's/^OpenTofu v\([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
read -r tofu_major tofu_minor <<<"${tofu_ver:-0 0}"
if (( tofu_major < 1 || (tofu_major == 1 && tofu_minor < 8) )); then
  die "OpenTofu >= 1.8 is required (variables in the encryption block); found: $(tofu version 2>/dev/null | head -1). Upgrade: winget upgrade OpenTofu.Tofu"
fi

if [[ $CONFIRM -ne 1 ]]; then
  cat >&2 <<'EOF'
Refusing to run: scripts/bootstrap.sh creates a GCP project, enables billable APIs, links billing,
and (without --gcp-only) creates a private GitHub repository. Re-run with one of:

  scripts/bootstrap.sh --gcp-only --i-understand-this-creates-gcp-resources   (GCP + Vertex only)
  scripts/bootstrap.sh --i-understand-this-creates-gcp-resources              (full, adds GitHub)

Read docs/plan.md ("Repository, commits and the GCP bootstrap") first. Agents must never run this.
EOF
  exit 2
fi

cd "$JR_ROOT"
REGION="${JR_REGION:-australia-southeast1}"
NAME_PREFIX="${NAME_PREFIX:-jr}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
WEBHOOK_PATH="${WEBHOOK_PATH:-/hooks/github}"
CLAUDE_MODELS=(claude-opus-5-5 claude-sonnet-5-5 claude-haiku-5-5)

CFG="$JR_CONFIG_DIR"
ANSWERS="$CFG/bootstrap.env"
BOOT_TFVARS="$CFG/bootstrap.tfvars"
PROD_TFVARS="$CFG/prod.tfvars"
DENYLIST="$CFG/denylist.txt"
BOOT_DIR="infra/bootstrap"
FIN_DIR="infra/github-finalize"
umask 077
mkdir -p "$CFG"; chmod 700 "$CFG"

# ------------------------------------------------------------------ helpers
tty_read() { # prompt default -> answer
  local ans
  read -r -p "$1${2:+ [$2]}: " ans </dev/tty || true
  printf '%s\n' "${ans:-$2}"
}
confirm() { [[ "$(tty_read "$1 (y/N)" "")" =~ ^[Yy]$ ]]; }
save_answer() { # key value  (shell-quoted, re-sourced on the next run)
  touch "$ANSWERS"
  grep -v "^$1=" "$ANSWERS" > "$ANSWERS.tmp" || true
  printf '%s=%q\n' "$1" "$2" >> "$ANSWERS.tmp"
  mv "$ANSWERS.tmp" "$ANSWERS"
}
deny_add() { # value...: append identifiers to the untracked denylist (deduplicated)
  touch "$DENYLIST"
  local v
  for v in "$@"; do
    [[ ${#v} -ge 4 ]] || continue
    grep -qxF -- "$v" "$DENYLIST" || printf '%s\n' "$v" >> "$DENYLIST"
  done
}
email_ok() { [[ "$1" =~ ^[^@[:space:]]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; }
tofu_out() { tofu -chdir="$BOOT_DIR" output -raw "$1" 2>/dev/null; }
hcl_str() { printf '"%s"' "${1//\"/\\\"}"; }
hcl_null_or_str() { if [[ -z "$1" ]]; then echo null; else hcl_str "$1"; fi; }

# shellcheck disable=SC1090
[[ -f "$ANSWERS" ]] && source "$ANSWERS"

# ------------------------------------------------------------------ 1. tools
missing=()
tools=(gcloud uv curl openssl)
[[ $GCP_ONLY -eq 1 ]] || tools+=(gh ssh-keygen)
for t in "${tools[@]}"; do have "$t" || missing+=("$t"); done
pnpm_cmd >/dev/null || missing+=("pnpm (or corepack)")
[[ ${#missing[@]} -eq 0 ]] || die "missing tools: ${missing[*]}"
docker_ok || warn "Docker not available: fine for bootstrap, but local agent sandboxes need it"
PY="$(command -v python3 || command -v python)" || die "python is required"

# ------------------------------------------------------------------ 2. sign-ins
if ! gcloud auth print-access-token >/dev/null 2>&1; then
  log "signing in to gcloud (browser)"; gcloud auth login
fi
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  log "creating Application Default Credentials (browser)"; gcloud auth application-default login
fi
GH_USER="" GH_PLAN=""
if [[ $GCP_ONLY -eq 0 ]]; then
  if ! gh auth status >/dev/null 2>&1; then
    log "signing in to GitHub (browser)"; gh auth login --hostname github.com --git-protocol ssh --web
  fi
  GH_USER="$(gh api user --jq .login)"
  GH_PLAN="$(gh api user --jq '.plan.name // "unknown"')"
else
  # Nothing below may use GitHub credentials; the GitHub provider runs with no token.
  unset GITHUB_TOKEN GH_TOKEN
fi

# ================================================================== --finalize
if [[ $FINALIZE -eq 1 ]]; then
  : "${PROJECT_ID:?run the bootstrap first (no saved PROJECT_ID in $ANSWERS)}"
  : "${GH_OWNER:?}" "${GH_REPO:?}"
  STATE_BUCKET="$(tofu_out state_bucket)" || die "cannot read bootstrap outputs; run the bootstrap first"
  repo="$GH_OWNER/$GH_REPO"
  if ! gh api "repos/$repo/rulesets" >/dev/null 2>&1; then
    die "GitHub refused the rulesets API for $repo (plan: $GH_PLAN). Private-repo rulesets need GitHub Pro or Team."
  fi
  [[ -n "$(git ls-remote "https://github.com/$repo.git" refs/heads/main 2>/dev/null)" ]] \
    || die "main has not been pushed yet; finalize runs after the initial commit's deploy"
  cat > "$FIN_DIR/backend_override.tf" <<EOF
# Written by scripts/bootstrap.sh (gitignored). State lives in the bootstrap state bucket.
terraform {
  backend "gcs" {
    bucket = "$STATE_BUCKET"
    prefix = "github-finalize"
  }
}
EOF
  export GITHUB_TOKEN; GITHUB_TOKEN="$(gh auth token)"
  export GITHUB_OWNER="$GH_OWNER"
  tofu -chdir="$FIN_DIR" init -input=false -reconfigure
  tofu -chdir="$FIN_DIR" apply -input=false -var "github_owner=$GH_OWNER" -var "github_repo=$GH_REPO"
  ok "rulesets applied"

  # Only `web` is public; it proxies /hooks/github to the IAM-only api.
  coord="$(gcloud run services describe "$NAME_PREFIX-web" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"
  if [[ -z "$coord" ]]; then
    warn "web service not found; webhook not registered (re-run --finalize after the deploy)"
  elif gh api "repos/$repo/hooks" --jq '.[].config.url' | grep -qF "$coord$WEBHOOK_PATH"; then
    ok "webhook already registered"
  else
    hmac="$(gcloud secrets versions access latest --secret webhook-hmac --project "$PROJECT_ID")"
    gh api "repos/$repo/hooks" --method POST --silent \
      -f name=web -F active=true \
      -f "events[]=check_run" -f "events[]=pull_request" -f "events[]=pull_request_review" \
      -f "config[url]=$coord$WEBHOOK_PATH" -f "config[content_type]=json" \
      -f "config[secret]=$hmac" -f "config[insecure_ssl]=0"
    unset hmac
    ok "webhook registered (check_run, pull_request, pull_request_review)"
  fi
  ok "finalize done"
  exit 0
fi

# ------------------------------------------------------------------ 3. organization
ORG_ID="${ORG_ID:-}"
if [[ -z "$ORG_ID" && "${VPC_SC:-}" != disabled ]]; then
  orgs="$(gcloud organizations list --format='value(name)' 2>/dev/null | sed 's#organizations/##')"
  n_orgs="$(printf '%s' "$orgs" | grep -c . || true)"
  if [[ "$n_orgs" -eq 1 ]]; then
    ORG_ID="$orgs"; log "using your Google Cloud Organization"
  elif [[ "$n_orgs" -gt 1 ]]; then
    gcloud organizations list --format='table(name.basename():label=ID,displayName)' >&2
    ORG_ID="$(tty_read "Organization ID to use" "")"
  else
    cat >&2 <<'EOF'

No Google Cloud Organization found. VPC Service Controls needs one (docs/plan.md "Sandboxing").
Create one for free with Cloud Identity Free, for a domain you control:

  1. Open https://workspace.google.com/gcpidentity/signup?sku=identitybasic and sign up with an
     admin account on your domain (it can be a new address on that domain).
  2. The Admin console shows a DNS TXT verification record, of the form:
         Host: @     Type: TXT     Value: google-site-verification=<token shown in the console>
     Add exactly that record at your DNS registrar, then click "Verify" in the Admin console.
  3. Sign in to gcloud as that admin (or grant your account Organization Administrator), then
     the Organization appears in `gcloud organizations list` (usually within minutes of verifying).

This script waits and polls every 30 seconds. Press Ctrl-C to stop; re-running resumes here.
EOF
    for ((i = 1; i <= 120; i++)); do
      orgs="$(gcloud organizations list --format='value(name)' 2>/dev/null | sed 's#organizations/##' | head -1)"
      [[ -n "$orgs" ]] && { ORG_ID="$orgs"; break; }
      if (( i % 10 == 0 )); then
        if confirm "Still no Organization after $((i / 2)) minutes. Give up and continue WITHOUT VPC-SC (deny-all egress + Private Google Access fallback, ADR-0006)?"; then
          VPC_SC=disabled; save_answer VPC_SC disabled; break
        fi
      fi
      sleep 30
    done
    [[ -n "$ORG_ID" || "${VPC_SC:-}" == disabled ]] || die "no Organization; re-run when Cloud Identity verification is done"
  fi
  [[ -n "$ORG_ID" ]] && save_answer ORG_ID "$ORG_ID"
fi

# ------------------------------------------------------------------ 4. billing, accounts, names
if [[ -z "${BILLING_ACCOUNT:-}" ]]; then
  mapfile -t accounts < <(gcloud billing accounts list --filter='open=true' --format='value(name.basename())')
  [[ ${#accounts[@]} -gt 0 ]] || die "no open billing account; create one at https://console.cloud.google.com/billing"
  if [[ ${#accounts[@]} -eq 1 ]]; then
    BILLING_ACCOUNT="${accounts[0]}"
  else
    gcloud billing accounts list --filter='open=true' --format='table(name.basename():label=ID,displayName)' >&2
    BILLING_ACCOUNT="$(tty_read "Billing account ID to use" "${accounts[0]}")"
  fi
  save_answer BILLING_ACCOUNT "$BILLING_ACCOUNT"
fi

if [[ -z "${OWNER_EMAIL:-}" || -z "${SECOND_EMAIL:-}" ]]; then
  log "the two app accounts (they go into the APP_USERS secret and the denylist, never the repo)"
  OWNER_EMAIL="$(tty_read "Owner account email" "")"; email_ok "$OWNER_EMAIL" || die "invalid email"
  SECOND_EMAIL="$(tty_read "Second account email" "")"; email_ok "$SECOND_EMAIL" || die "invalid email"
  ALERT_EMAIL="$(tty_read "Budget/alert email" "$OWNER_EMAIL")"; email_ok "$ALERT_EMAIL" || die "invalid email"
  save_answer OWNER_EMAIL "$OWNER_EMAIL"; save_answer SECOND_EMAIL "$SECOND_EMAIL"; save_answer ALERT_EMAIL "$ALERT_EMAIL"
fi
if [[ -z "${GH_OWNER:-}" ]]; then
  log "the GitHub repo is named now (the WIF conditions pin it) but created only by the full run"
  GH_OWNER="$(tty_read "GitHub owner (login) for the private repo" "$GH_USER")"
  [[ "$GH_OWNER" =~ ^[A-Za-z0-9-]+$ ]] || die "invalid GitHub login"
  GH_REPO="$(tty_read "GitHub repository name" "job-radar")"
  [[ "$GH_REPO" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid repository name"
  save_answer GH_OWNER "$GH_OWNER"; save_answer GH_REPO "$GH_REPO"
fi
# The owner's numeric id pins the WIF conditions (a renamed login can't be re-registered into them).
if [[ $GCP_ONLY -eq 0 ]]; then
  gh_id="$(gh api "users/$GH_OWNER" --jq .id)" || die "cannot read the GitHub id of $GH_OWNER"
  [[ -z "${GH_OWNER_ID:-}" || "$GH_OWNER_ID" == "$gh_id" ]] \
    || die "saved GH_OWNER_ID differs from GitHub's id for $GH_OWNER; fix $ANSWERS"
  GH_OWNER_ID="$gh_id"; save_answer GH_OWNER_ID "$GH_OWNER_ID"
elif [[ -z "${GH_OWNER_ID:-}" ]]; then
  # One anonymous read of public profile data; nothing is written to GitHub.
  GH_OWNER_ID="$(curl -fsS -m 20 -H 'Accept: application/vnd.github+json' "https://api.github.com/users/$GH_OWNER" 2>/dev/null \
    | run_py -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null || true)"
  if [[ -z "$GH_OWNER_ID" ]]; then
    warn "could not read the numeric GitHub id of $GH_OWNER (offline or rate-limited)"
    GH_OWNER_ID="$(tty_read "Numeric id (field \"id\" at https://api.github.com/users/$GH_OWNER)" "")"
  fi
  [[ "$GH_OWNER_ID" =~ ^[0-9]+$ ]] || die "the GitHub owner id must be numeric"
  log "GitHub owner id saved; the full run re-checks it against GitHub"
  save_answer GH_OWNER_ID "$GH_OWNER_ID"
fi
if [[ $GCP_ONLY -eq 0 ]]; then
  case "$GH_PLAN" in
    free) die "GitHub plan is Free: private-repo rulesets and environment reviewers need Pro or Team (docs/plan.md)" ;;
    unknown) warn "could not read the GitHub plan; the M0 spike must confirm rulesets and environment reviewers" ;;
  esac
elif [[ "${GITHUB_ENABLED:-0}" == 1 ]]; then
  die "the GitHub parts already exist (GITHUB_ENABLED=1 in $ANSWERS); --gcp-only would remove them. Run without --gcp-only."
fi
if [[ -z "${PROJECT_ID:-}" ]]; then
  PROJECT_ID="jobradar-$(openssl rand -hex 3)"
  save_answer PROJECT_ID "$PROJECT_ID"
  log "new project id generated (saved in $ANSWERS)"
fi
STATE_BUCKET_NAME="${PROJECT_ID}-tofu-state"
deny_add "$PROJECT_ID" "$BILLING_ACCOUNT" "$OWNER_EMAIL" "$SECOND_EMAIL" "$ALERT_EMAIL" \
  "${OWNER_EMAIL#*@}" "${SECOND_EMAIL#*@}" "$STATE_BUCKET_NAME" "$GH_OWNER" "$GH_OWNER_ID"
[[ -n "$ORG_ID" ]] && deny_add "$ORG_ID"

cat > "$BOOT_TFVARS" <<EOF
# Written by scripts/bootstrap.sh. UNTRACKED; never copy into the repo.
project_id        = $(hcl_str "$PROJECT_ID")
project_name      = "Job Radar"
org_id            = $(hcl_null_or_str "$ORG_ID")
billing_account   = $(hcl_str "$BILLING_ACCOUNT")
region            = $(hcl_str "$REGION")
state_bucket_name = $(hcl_str "$STATE_BUCKET_NAME")
state_key_users   = [$(hcl_str "user:$(gcloud config get-value account 2>/dev/null)")]
alert_email       = $(hcl_str "$ALERT_EMAIL")
enable_github     = $([[ $GCP_ONLY -eq 1 ]] && echo false || echo true)
github_owner      = $(hcl_str "$GH_OWNER")
github_owner_id   = $(hcl_str "$GH_OWNER_ID")
github_repo       = $(hcl_str "$GH_REPO")
github_reviewer   = $(hcl_null_or_str "$GH_USER")
EOF
ok "wrote $BOOT_TFVARS"

# ------------------------------------------------------------------ 5. infra/bootstrap
if [[ $GCP_ONLY -eq 0 ]]; then
  export GITHUB_TOKEN; GITHUB_TOKEN="$(gh auth token)"
  export GITHUB_OWNER="$GH_OWNER"
fi
# Never let --gcp-only plan the removal of GitHub resources a full run created.
guard_gcp_only() {
  [[ $GCP_ONLY -eq 1 ]] || return 0
  if [[ "$(tofu_out github_enabled || true)" == true ]] \
     || tofu -chdir="$BOOT_DIR" state list 2>/dev/null | grep -qE '^(data\.)?github_'; then
    save_answer GITHUB_ENABLED 1
    die "the bootstrap state already manages GitHub resources; --gcp-only would remove them. Run without --gcp-only."
  fi
}
boot_apply() {
  guard_gcp_only
  tofu -chdir="$BOOT_DIR" apply -input=false -var-file="$(native_path "$BOOT_TFVARS")"
}
override="$BOOT_DIR/backend_override.tf"
enc="$BOOT_DIR/encryption.tofu"

if ! grep -qs 'backend "gcs"' "$override"; then
  log "pass 1: local state, encryption off (the KMS key doesn't exist yet)"
  cat > "$override" <<EOF
# Written by scripts/bootstrap.sh (gitignored). Pass 1: local state outside the repo.
terraform {
  backend "local" {
    path = "$(native_path "$CFG/bootstrap.tfstate" | sed 's#\\#/#g')"
  }
}
EOF
  [[ -f "$enc" ]] && mv "$enc" "$enc.off"
  trap '[[ -f "$enc.off" ]] && mv "$enc.off" "$enc"' EXIT
  tofu -chdir="$BOOT_DIR" init -input=false -reconfigure
  boot_apply
  mv "$enc.off" "$enc"; trap - EXIT

  log "pass 2: migrating state to GCS with KMS client-side encryption"
  bucket="$(tofu_out state_bucket)"
  cat > "$override" <<EOF
# Written by scripts/bootstrap.sh (gitignored). Pass 2: encrypted state in the GCS bucket.
terraform {
  backend "gcs" {
    bucket = "$bucket"
    prefix = "bootstrap"
  }
}
EOF
  tofu -chdir="$BOOT_DIR" init -input=false -migrate-state -force-copy
  warn "state migrated. Now remove the 'fallback' blocks in $enc and set enforced = true (see its header)."
fi
tofu -chdir="$BOOT_DIR" init -input=false
boot_apply
[[ $GCP_ONLY -eq 1 ]] || save_answer GITHUB_ENABLED 1

PROJECT_NUMBER="$(tofu_out project_number)"
STATE_BUCKET="$(tofu_out state_bucket)"
STATE_KMS_KEY="$(tofu_out state_kms_key)"
DEPLOY_SA="$(tofu_out deploy_service_account)"
REVIEW_SA="$(tofu_out review_service_account)"
NOTIFY="$(tofu_out notification_channel)"
REPO_FULL=""
[[ $GCP_ONLY -eq 1 ]] || REPO_FULL="$(tofu_out github_repository)"
deny_add "$PROJECT_NUMBER" "$STATE_BUCKET" "$DEPLOY_SA" "$REVIEW_SA" "${PROJECT_ID}-uploads" "${PROJECT_ID}-artifacts"

cat > "$PROD_TFVARS" <<EOF
# Written by scripts/bootstrap.sh. UNTRACKED; the full run mirrors it to the GitHub prod-environment secret PROD_TFVARS.
project_id             = $(hcl_str "$PROJECT_ID")
project_number         = $(hcl_str "$PROJECT_NUMBER")
region                 = $(hcl_str "$REGION")
org_id                 = $(hcl_null_or_str "$ORG_ID")
name_prefix            = $(hcl_str "$NAME_PREFIX")
state_encryption_key   = $(hcl_str "$STATE_KMS_KEY")
deploy_service_account = $(hcl_str "$DEPLOY_SA")
review_service_account = $(hcl_str "$REVIEW_SA")
notification_channels  = [$(hcl_str "$NOTIFY")]
uploads_bucket_name    = $(hcl_str "${PROJECT_ID}-uploads")
artifacts_bucket_name  = $(hcl_str "${PROJECT_ID}-artifacts")
EOF
ok "wrote $PROD_TFVARS"

# ------------------------------------------------------------------ 6. secrets (outside OpenTofu)
# envs/prod (module "secrets") owns the secret containers. On a first bootstrap they don't exist
# yet, so this creates them (label managed-by=bootstrap); envs/prod must import them by id.
secret_ensure() { # id
  gcloud secrets describe "$1" --project "$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud secrets create "$1" --project "$PROJECT_ID" --replication-policy=user-managed \
         --locations="$REGION" --labels=managed-by=bootstrap --quiet >/dev/null
}
secret_has_version() {
  [[ -n "$(gcloud secrets versions list "$1" --project "$PROJECT_ID" --filter='state=enabled' --limit=1 --format='value(name)' 2>/dev/null)" ]]
}
secret_put() { # id (value on stdin); adds a version only when the value changed
  secret_ensure "$1"
  local new; new="$(cat)"
  if secret_has_version "$1" && [[ "$(gcloud secrets versions access latest --secret "$1" --project "$PROJECT_ID")" == "$new" ]]; then
    return 0
  fi
  printf '%s' "$new" | gcloud secrets versions add "$1" --project "$PROJECT_ID" --data-file=- --quiet >/dev/null
  log "secret $1 updated"
}

printf '[{"email":"%s","role":"owner"},{"email":"%s","role":"member"}]' "$OWNER_EMAIL" "$SECOND_EMAIL" | secret_put app-users
secret_put denylist < "$DENYLIST"

if [[ $GCP_ONLY -eq 1 ]]; then
  log "GCP-only: deploy key, webhook HMAC and GitHub Actions secrets are left for the full run"
elif gh repo deploy-key list -R "$REPO_FULL" 2>/dev/null | grep -q 'jobradar-publisher'; then
  ok "deploy key already registered"
else
  keydir="$(mktemp -d)"
  ssh-keygen -q -t ed25519 -N "" -C "jobradar-publisher" -f "$keydir/key"
  secret_ensure deploy-key
  gcloud secrets versions add deploy-key --project "$PROJECT_ID" --data-file="$(native_path "$keydir/key")" --quiet >/dev/null
  gh repo deploy-key add "$keydir/key.pub" -R "$REPO_FULL" --allow-write --title jobradar-publisher
  rm -rf "$keydir"
  ok "deploy key generated: private half in Secret Manager only, public half on GitHub"
fi

if [[ $GCP_ONLY -eq 0 ]]; then
  secret_ensure webhook-hmac
  if secret_has_version webhook-hmac; then
    ok "webhook HMAC already set"
  else
    openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add webhook-hmac --project "$PROJECT_ID" --data-file=- --quiet >/dev/null
    ok "webhook HMAC generated (registered on GitHub by --finalize)"
  fi

  gh secret set PROD_TFVARS --env prod -R "$REPO_FULL" < "$PROD_TFVARS"
  gh secret set JOBRADAR_DENYLIST -R "$REPO_FULL" < "$DENYLIST"
  ok "GitHub secrets PROD_TFVARS (prod env) and JOBRADAR_DENYLIST set"
fi

# ------------------------------------------------------------------ 7. Vertex access checks
token() { gcloud auth application-default print-access-token; }
vertex_call() { # url json -> http code
  curl -sS -o /dev/null -w '%{http_code}' -m 60 -X POST "$1" \
    -H "Authorization: Bearer $(token)" -H "Content-Type: application/json" \
    -H "x-goog-user-project: $PROJECT_ID" -d "$2" || echo 000
}
check_model() { # label url body console-link
  local code
  while :; do
    code="$(vertex_call "$2" "$3")"
    case "$code" in
      200) ok "$1: access OK"; return 0 ;;
      429) warn "$1: reachable but quota-limited (429). Request quota: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID" ; return 0 ;;
      *)
        warn "$1: test call returned HTTP $code."
        echo "  Enable the model / accept its terms here, then press Enter to retry (s = skip):" >&2
        echo "  $4" >&2
        [[ "$(tty_read "  retry" "")" == s ]] && { warn "$1: skipped; features using it will fail"; return 0; }
        ;;
    esac
  done
}
claude_body='{"anthropic_version":"vertex-2023-10-16","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}'
for m in "${CLAUDE_MODELS[@]}"; do
  check_model "$m" \
    "https://aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/global/publishers/anthropic/models/$m:rawPredict" \
    "$claude_body" \
    "https://console.cloud.google.com/vertex-ai/publishers/anthropic/model-garden/$m?project=$PROJECT_ID"
done
check_model "$GEMINI_MODEL" \
  "https://aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/global/publishers/google/models/$GEMINI_MODEL:generateContent" \
  '{"contents":[{"role":"user","parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":1}}' \
  "https://console.cloud.google.com/vertex-ai/model-garden?project=$PROJECT_ID"
if [[ -n "${MAAS_MODEL:-}" ]]; then
  maas_region="${MAAS_REGION:-us-central1}"
  check_model "$MAAS_MODEL (MaaS)" \
    "https://${maas_region}-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$maas_region/endpoints/openapi/chat/completions" \
    "{\"model\":\"$MAAS_MODEL\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    "https://console.cloud.google.com/vertex-ai/model-garden?project=$PROJECT_ID"
else
  log "MAAS_MODEL not set: the open-weight model is pinned by eval in M1; re-run with MAAS_MODEL=<id> to check it"
fi

# ------------------------------------------------------------------ 8. summary
vpcsc_line="$([[ -n "$ORG_ID" ]] && echo "enabled (Organization found)" || echo "DISABLED: fallback per ADR-0006")"
if [[ $GCP_ONLY -eq 1 ]]; then
  cat >&2 <<EOF

GCP-only bootstrap complete. Nothing was created or written on GitHub.
  Created in GCP:    project (billing linked, APIs enabled); state bucket + KMS state key (state
                     migrated, encrypted); Artifact Registry (images); budget + email alert channel;
                     WIF pools/providers deploy + review, pinned to $GH_OWNER/$GH_REPO on main;
                     service accounts gh-deploy + gh-security-review; secrets app-users + denylist
  Local (untracked): $CFG/{bootstrap.env,bootstrap.tfvars,prod.tfvars,denylist.txt}
  VPC-SC:            $vpcsc_line
  Vertex:            checked above; local agents use your ADC with quota project $PROJECT_ID
  Request the Vertex Claude quota now: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID

Estimated monthly cost of this footprint (USD list prices, idle; check the GCP pricing calculator):
  Budget + alert channel        0.00  free
  State bucket (a few KB)      <0.01  Standard storage, versioned
  KMS state key                 0.06  per enabled key version; +0.06 for each 90-day rotation kept
  Artifact Registry             0.00  under the 0.5 GB free tier (0.10 per GB-month above it)
  Secret Manager (2 secrets)    0.00  within the free tier (6 active versions); else 0.06 each
  WIF, service accounts, APIs   0.00
  Total                        ~0.10 / month, plus pay-per-token Vertex calls you make
  Not in bootstrap: Cloud SQL, Cloud Run, VPC, Model Armor, SDP (envs/prod, the first deploy).

Later, at initial-commit time (only when you ask for it):
  scripts/bootstrap.sh --i-understand-this-creates-gcp-resources
    adds on top, idempotently: gh sign-in + plan check, the private repo, the prod environment and
    reviewer, Actions variables, deploy key, webhook HMAC, PROD_TFVARS + JOBRADAR_DENYLIST secrets.
  Then the initial commit; after its first deploy: scripts/bootstrap.sh --finalize --i-understand-this-creates-gcp-resources
EOF
  exit 0
fi

cat >&2 <<EOF

Bootstrap complete.
  Local config (untracked):  $CFG/{bootstrap.env,bootstrap.tfvars,prod.tfvars,denylist.txt}
  OpenTofu state:            GCS bucket (encrypted), prefix "bootstrap"
  GitHub:                    private repo, prod environment (you approve), Actions variables,
                             deploy key, secrets PROD_TFVARS + JOBRADAR_DENYLIST
  VPC-SC:                    $vpcsc_line
  Request the Vertex Claude quota on day 1: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID

Next: the initial commit (only when you ask), authored with your GitHub noreply email.
After its first deploy: scripts/bootstrap.sh --finalize --i-understand-this-creates-gcp-resources
EOF
