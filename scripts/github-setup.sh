#!/usr/bin/env bash
# One-time GitHub setup for CODE_HOST=github (docs/features/github-prs.md "One-time setup").
# RUN BY THE OWNER (it changes the GitHub repo's settings with your gh login). Idempotent: safe
# to re-run. Prints what it does; never prints key material.
#
#   bash scripts/github-setup.sh --repo <owner/name>      (or GITHUB_REPO=<owner/name>)
#
# What it does:
#   1. Generates an ed25519 deploy key at GITHUB_DEPLOY_KEY_PATH
#      (default ~/.config/jobradar/github_deploy_key, mode 600), unless one already exists.
#   2. Writes the pinned github.com host key to GITHUB_KNOWN_HOSTS_PATH
#      (default ~/.config/jobradar/github_known_hosts), after checking its fingerprint.
#   3. Registers the public key on the repo with write access (`gh repo deploy-key add
#      --allow-write`), unless the same key is already there.
#   4. Adds (or updates) the `jobradar-main` ruleset on main: a PR with one approval is
#      required, force pushes and deletion are blocked, repository admins can bypass. The
#      deploy key then can't push to main; agents push only to agent/*.
#   5. Lets GitHub Actions create PRs (agent-pr.yml uses GITHUB_TOKEN) while keeping the
#      default workflow token read-only; the workflows declare their own permissions.
# Then set CODE_HOST=github and GITHUB_REPO in ~/.config/jobradar/local.env.
set -euo pipefail

REPO="${GITHUB_REPO:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ ! "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || [[ "$REPO" =~ (^|/)\.\.?(/|$) ]]; then
  echo "error: pass --repo <owner/name> (or set GITHUB_REPO)" >&2
  exit 2
fi

CONFIG_DIR="$HOME/.config/jobradar"
KEY="${GITHUB_DEPLOY_KEY_PATH:-$CONFIG_DIR/github_deploy_key}"
KNOWN_HOSTS="${GITHUB_KNOWN_HOSTS_PATH:-$CONFIG_DIR/github_known_hosts}"
KEY_TITLE="jobradar-agent-push"
RULESET_NAME="jobradar-main"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# github.com's published ed25519 host key
# (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
# Its fingerprint must be SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU; checked below with
# `ssh-keygen -lf` before the file is written.
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
GITHUB_HOST_FINGERPRINT="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is not installed" >&2; exit 1; }; }
need gh
need ssh-keygen
gh auth status >/dev/null 2>&1 || { echo "error: gh is not signed in (run: gh auth login)" >&2; exit 1; }

inside_repo() {
  local dir
  dir="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || return 1
  [[ "$dir" == "$ROOT" || "$dir" == "$ROOT"/* ]]
}

for path in "$KEY" "$KNOWN_HOSTS"; do
  mkdir -p "$(dirname "$path")"
  chmod 700 "$(dirname "$path")" 2>/dev/null || true
  if inside_repo "$path"; then
    echo "error: key files must live outside the project folder" >&2
    exit 1
  fi
done

# 1. Deploy key.
if [[ -f "$KEY" ]]; then
  echo "deploy key: already exists, kept"
else
  ssh-keygen -q -t ed25519 -N "" -C "$KEY_TITLE" -f "$KEY" >/dev/null
  echo "deploy key: generated"
fi
chmod 600 "$KEY"
if [[ ! -f "$KEY.pub" ]]; then
  ssh-keygen -y -f "$KEY" > "$KEY.pub"
fi

# 2. Pinned github.com host key.
tmp_known="$(mktemp)"
trap 'rm -f "$tmp_known"' EXIT
printf '%s\n' "$GITHUB_HOST_KEY" > "$tmp_known"
if ! ssh-keygen -lf "$tmp_known" | grep -qF "$GITHUB_HOST_FINGERPRINT"; then
  echo "error: the pinned github.com host key does not match its published fingerprint" >&2
  exit 1
fi
if [[ -f "$KNOWN_HOSTS" ]] && cmp -s "$tmp_known" "$KNOWN_HOSTS"; then
  echo "known_hosts: already pinned"
else
  cp "$tmp_known" "$KNOWN_HOSTS"
  chmod 644 "$KNOWN_HOSTS"
  echo "known_hosts: pinned github.com ($GITHUB_HOST_FINGERPRINT)"
fi

# 3. Register the deploy key with write access (compares type + key body only).
public_body="$(awk '{print $1" "$2}' "$KEY.pub")"
existing="$(gh api "repos/$REPO/keys" --paginate --jq '.[] | "\(.key)\t\(.read_only)"')"
match="$(printf '%s\n' "$existing" | awk -F'\t' -v k="$public_body" '$1 == k {print $2}' | head -1)"
if [[ "$match" == "false" ]]; then
  echo "deploy key on $REPO: already registered with write access"
elif [[ "$match" == "true" ]]; then
  echo "error: the deploy key is registered read-only on $REPO; remove it there and re-run" >&2
  exit 1
else
  gh repo deploy-key add "$KEY.pub" --repo "$REPO" --allow-write --title "$KEY_TITLE" >/dev/null
  echo "deploy key on $REPO: registered with write access"
fi

# 4. Ruleset on main: PR required (one approval), no force pushes, no deletion; admins bypass.
ruleset_json="$(cat <<JSON
{
  "name": "$RULESET_NAME",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
  "rules": [
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false
    }},
    {"type": "non_fast_forward"},
    {"type": "deletion"}
  ]
}
JSON
)"
ruleset_id="$(gh api "repos/$REPO/rulesets" --paginate \
  --jq ".[] | select(.name == \"$RULESET_NAME\") | .id" | head -1)"
if [[ -n "$ruleset_id" ]]; then
  printf '%s' "$ruleset_json" | gh api -X PUT "repos/$REPO/rulesets/$ruleset_id" --input - >/dev/null
  echo "ruleset $RULESET_NAME on main: updated"
else
  printf '%s' "$ruleset_json" | gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null
  echo "ruleset $RULESET_NAME on main: created"
fi

# 5. Actions may open PRs with GITHUB_TOKEN; the default token stays read-only.
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null
echo "workflow permissions: default read, Actions may create PRs"

echo
echo "Done. Add to ~/.config/jobradar/local.env:"
echo "  CODE_HOST=github"
echo "  GITHUB_REPO=$REPO"
