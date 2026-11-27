#!/usr/bin/env bash
# Real sign-in for local development: Identity Platform email/password for exactly two accounts.
# RUN BY THE OWNER (it changes your GCP project). Idempotent: safe to re-run.
#
#   bash scripts/setup-identity.sh --project <gcp-project> [--owner <email>] [--tester <email>] [--mfa] [--print-links] [--set-passwords] [--action-url <url>]
#
# What it does in <gcp-project>:
#   1. Enables identitytoolkit, securetoken and apikeys APIs; initialises Identity Platform.
#   2. Email/password sign-in ON, self-service sign-up OFF, account self-deletion OFF,
#      authorised domains localhost + 127.0.0.1 (+ the project's own). --mfa also enables TOTP MFA.
#   3. Creates the two accounts (if missing) and sends each a password-setup email whose link opens
#      the app's own /auth/action page (--action-url; --print-links prints it instead), or with
#      --set-passwords sets each password right here from a hidden prompt (no email needed).
#   4. Creates a browser API key restricted to the local web origins and the auth APIs.
#   5. Writes AUTH=identity_platform, IDP_PROJECT_ID, APP_USERS (owner = maintainer,
#      tester = user), NEXT_PUBLIC_AUTH_MODE=identity and NEXT_PUBLIC_FIREBASE_API_KEY into
#      ~/.config/jobradar/local.env (outside the repo; existing keys are replaced, others kept).
# Emails never enter the repo. Restart scripts/dev.sh afterwards. To go back to fake sign-in,
# set AUTH=fake and NEXT_PUBLIC_AUTH_MODE=fake in local.env.
set -euo pipefail

PROJECT="" OWNER="" TESTER="" MFA=0 PRINT_LINKS=0 SET_PASSWORDS=0
ACTION_URL="http://127.0.0.1:3000/auth/action"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    --tester) TESTER="$2"; shift 2 ;;
    --mfa) MFA=1; shift ;;
    --print-links) PRINT_LINKS=1; shift ;;
    # Set each password here (hidden prompt) instead of emailing a link. Use this when the
    # emailed links don't work yet; the password never appears in args, logs or files.
    --set-passwords|--set-password) SET_PASSWORDS=1; shift ;;
    # Where password-reset / verify-email links land (the app's own handler, not firebaseapp.com).
    --action-url) ACTION_URL="$2"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

die() { echo "setup-identity: $*" >&2; exit 1; }
say() { echo "[identity] $*"; }
command -v gcloud >/dev/null || die "gcloud is required"
command -v python >/dev/null || command -v python3 >/dev/null || die "python is required (JSON handling)"
PY=$(command -v python || command -v python3)
[[ -n "$PROJECT" ]] || die "--project is required"
[[ -n "$OWNER" ]] || read -r -p "Owner (maintainer) email: " OWNER
[[ -n "$TESTER" ]] || read -r -p "Tester (user) email: " TESTER
for e in "$OWNER" "$TESTER"; do
  [[ "$e" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "not a plain email address: $e"
done
[[ "${OWNER,,}" != "${TESTER,,}" ]] || die "owner and tester must be different accounts"

TOKEN=$(gcloud auth print-access-token) || die "run: gcloud auth login"
api() { # method url [json]
  local method="$1" url="$2" body="${3:-}"
  curl -sS -X "$method" "$url" -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" \
    -H "Content-Type: application/json" ${body:+--data "$body"}
}
set_password() { # email: hidden prompt (twice), then admin accounts:update. The password only
  # travels via an env var into python and over stdin to curl: never in argv, logs or files.
  local email="$1" pw pw2 local_id body result
  local_id=$(api POST "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:lookup" \
    "{\"email\":[\"$email\"]}" | json_get "d['users'][0]['localId']")
  [[ -n "$local_id" ]] || die "couldn't look up $email"
  while true; do
    read -r -s -p "   New password for $email (12+ chars): " pw; echo
    read -r -s -p "   Repeat it: " pw2; echo
    if [[ "$pw" != "$pw2" ]]; then echo "   They don't match. Try again."; continue; fi
    if [[ ${#pw} -lt 12 ]]; then echo "   Use at least 12 characters."; continue; fi
    break
  done
  body=$(PW="$pw" "$PY" -c 'import json,os,sys; print(json.dumps({"localId": sys.argv[1], "password": os.environ["PW"], "emailVerified": True}))' "$local_id")
  unset pw pw2
  result=$(printf '%s' "$body" | curl -sS -X POST \
    "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:update" \
    -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" \
    -H "Content-Type: application/json" --data-binary @-)
  unset body
  echo "$result" | grep -q '"error"' && { echo "$result" | json_get "d['error'].get('message')" >&2; die "couldn't set the password for $email"; }
  say "   password set for $email"
}
json_get() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}) or '')" "$1"; }

say "1/5 enabling APIs and Identity Platform in $PROJECT"
gcloud services enable identitytoolkit.googleapis.com securetoken.googleapis.com apikeys.googleapis.com --project "$PROJECT"
init=$(api POST "https://identitytoolkit.googleapis.com/v2/projects/$PROJECT/identityPlatform:initializeAuth" '{}')
if echo "$init" | grep -q '"error"' && ! echo "$init" | grep -qi "already"; then
  echo "$init" | head -c 400 >&2; echo >&2
  die "couldn't initialise Identity Platform (check billing is enabled and you have Owner/Editor)"
fi

say "2/5 configuring: email/password on, sign-up off, deletion off$( [[ $MFA -eq 1 ]] && echo ', TOTP MFA on')"
[[ "$ACTION_URL" =~ ^https?://[A-Za-z0-9.:-]+/[A-Za-z0-9/_-]*$ ]] || die "--action-url must be a plain http(s) URL"
mask="signIn.email,client.permissions,authorizedDomains"
mfa_json=""
if [[ $MFA -eq 1 ]]; then
  mask="$mask,mfa"
  mfa_json=',"mfa":{"state":"ENABLED","providerConfigs":[{"state":"ENABLED","totpProviderConfig":{"adjacentIntervals":5}}]}'
fi
cfg=$(api PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT/config?updateMask=$mask" \
  "{\"signIn\":{\"email\":{\"enabled\":true,\"passwordRequired\":true}},\"client\":{\"permissions\":{\"disabledUserSignup\":true,\"disabledUserDeletion\":true}},\"authorizedDomains\":[\"localhost\",\"127.0.0.1\",\"$PROJECT.firebaseapp.com\",\"$PROJECT.web.app\"]$mfa_json}")
echo "$cfg" | grep -q '"error"' && { echo "$cfg" | head -c 400 >&2; echo >&2; die "config update failed"; }
# Email action links -> the app's own /auth/action page. Some projects refuse this over the API
# (EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED); that only affects emailed links, so warn and carry on.
cb=$(api PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT/config?updateMask=notification.sendEmail.callbackUri"   "{\"notification\":{\"sendEmail\":{\"callbackUri\":\"$ACTION_URL\"}}}")
if echo "$cb" | grep -q '"error"'; then
  say "   note: couldn't set the email action URL ($(echo "$cb" | json_get "d['error'].get('message')"))."
  say "   Emailed reset links will still open the hosted page. Use --set-passwords, or set the action URL"
  say "   in the console: Identity Platform > Settings > Email templates > Action URL = $ACTION_URL"
else
  say "   email links now open $ACTION_URL"
fi

say "3/5 provisioning the two accounts"
for email in "$OWNER" "$TESTER"; do
  found=$(api POST "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:lookup" "{\"email\":[\"$email\"]}" | json_get "len(d.get('users', []))")
  if [[ "${found:-0}" == "0" ]]; then
    created=$(api POST "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts" "{\"email\":\"$email\",\"emailVerified\":true}")
    echo "$created" | grep -q '"error"' && { echo "$created" | head -c 300 >&2; echo >&2; die "couldn't create $email"; }
    say "   created $email"
  else
    say "   $email already exists"
  fi
  if [[ $SET_PASSWORDS -eq 1 ]]; then
    set_password "$email"
    continue
  fi
  link_flag=$([[ $PRINT_LINKS -eq 1 ]] && echo true || echo false)
  oob=$(api POST "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:sendOobCode" \
    "{\"requestType\":\"PASSWORD_RESET\",\"email\":\"$email\",\"returnOobLink\":$link_flag}")
  if [[ $PRINT_LINKS -eq 1 ]]; then
    say "   password link for $email: $(echo "$oob" | json_get "d.get('oobLink')")"
  else
    say "   password-setup email sent to $email"
  fi
done

say "4/5 browser API key (restricted to local web origins and the auth APIs)"
KEY_NAME=$(gcloud services api-keys list --project "$PROJECT" --filter="displayName=jobradar-web-local" --format="value(name)" | head -1)
if [[ -z "$KEY_NAME" ]]; then
  gcloud services api-keys create --project "$PROJECT" --display-name="jobradar-web-local" \
    --api-target=service=identitytoolkit.googleapis.com --api-target=service=securetoken.googleapis.com \
    --allowed-referrers="http://127.0.0.1:3000/*,http://localhost:3000/*" >/dev/null
  KEY_NAME=$(gcloud services api-keys list --project "$PROJECT" --filter="displayName=jobradar-web-local" --format="value(name)" | head -1)
fi
API_KEY=$(gcloud services api-keys get-key-string "$KEY_NAME" --format="value(keyString)")
[[ -n "$API_KEY" ]] || die "couldn't read the API key"

say "5/5 writing ~/.config/jobradar/local.env (values not shown)"
CONF="$HOME/.config/jobradar/local.env"
mkdir -p "$(dirname "$CONF")"; touch "$CONF"; chmod 600 "$CONF"
APP_USERS="[{\"email\":\"$OWNER\",\"role\":\"maintainer\"},{\"email\":\"$TESTER\",\"role\":\"user\"}]"
"$PY" - "$CONF" <<PYEOF
import sys
path = sys.argv[1]
updates = {
    "AUTH": "identity_platform",
    "IDP_PROJECT_ID": "$PROJECT",
    "APP_USERS": '$APP_USERS',
    "NEXT_PUBLIC_AUTH_MODE": "identity",
    "NEXT_PUBLIC_FIREBASE_API_KEY": "$API_KEY",
}
lines = [l for l in open(path, encoding="utf-8").read().splitlines()
         if l.split("=", 1)[0].strip() not in updates]
# Single-quote every value: dev.sh sources this file with bash, which would otherwise strip the
# double quotes inside JSON values such as APP_USERS (none of these values contain a quote).
lines += [f"{k}='{v}'" for k, v in updates.items()]
open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
PYEOF

say "done. Set each password from the email (or printed link), then restart: bash scripts/dev.sh"
say "Sign in at http://127.0.0.1:3000 with either account. Any other account is refused."
