# shellcheck shell=bash
# Shared helpers for scripts/*.sh. Source it; don't execute it.
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# Works in Git Bash (Windows), WSL, Linux and macOS.

if [[ -n "${_JR_COMMON_LOADED:-}" ]]; then return 0; fi
_JR_COMMON_LOADED=1

JR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JR_CONFIG_DIR="${JOBRADAR_CONFIG_DIR:-$HOME/.config/jobradar}"
JR_CACHE_DIR="${JOBRADAR_CACHE_DIR:-$HOME/.cache/jobradar}"
JR_ARTIFACTS="$JR_ROOT/.artifacts"

# Pinned tool versions (one place, so local and CI agree).
JR_PGSERVER_VERSION="0.1.4"
JR_DATAMODEL_CODEGEN_VERSION="0.88.0"
JR_RUFF_VERSION="0.17.4"
JR_OPENAPI_TS_VERSION="7.13.0"
JR_SPECTRAL_VERSION="6.17.0"
JR_PNPM_VERSION="9.15.9"
JR_PYTHON_VERSION="3.12"

if [[ -t 2 && -z "${NO_COLOR:-}" ]]; then
  _c_red=$'\e[31m'; _c_grn=$'\e[32m'; _c_ylw=$'\e[33m'; _c_dim=$'\e[2m'; _c_off=$'\e[0m'
else
  _c_red=""; _c_grn=""; _c_ylw=""; _c_dim=""; _c_off=""
fi

log()  { printf '%s[%s]%s %s\n' "$_c_dim" "${JR_SCRIPT:-jr}" "$_c_off" "$*" >&2; }
warn() { printf '%s[%s] warning:%s %s\n' "$_c_ylw" "${JR_SCRIPT:-jr}" "$_c_off" "$*" >&2; }
die()  { printf '%s[%s] error:%s %s\n' "$_c_red" "${JR_SCRIPT:-jr}" "$_c_off" "$*" >&2; exit 1; }
ok()   { printf '%s[%s]%s %s\n' "$_c_grn" "${JR_SCRIPT:-jr}" "$_c_off" "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

is_windows() {
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac
}

# Path as the native OS sees it (Windows tools need C:\..., not /c/...).
native_path() {
  if is_windows && have cygpath; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

docker_ok() { have docker && docker info >/dev/null 2>&1; }

# Echo the pnpm command to use: pnpm, then corepack, then npx.
pnpm_cmd() {
  if have pnpm; then echo "pnpm"
  elif have corepack; then echo "corepack pnpm@${JR_PNPM_VERSION}"
  elif have npx; then echo "npx -y pnpm@${JR_PNPM_VERSION}"
  else return 1; fi
}
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

# Run pnpm with whichever launcher exists.
run_pnpm() {
  local cmd
  cmd="$(pnpm_cmd)" || { warn "pnpm not found (and no corepack/npx)"; return 127; }
  # shellcheck disable=SC2086
  $cmd "$@"
}

# Echo "tofu" or "terraform" (fallback), or fail.
iac_cmd() {
  if have tofu; then echo tofu
  elif have terraform; then echo terraform
  else return 1; fi
}

# uv run for the backend project: --locked when a lockfile exists.
uv_backend() {
  if [[ -f "$JR_ROOT/backend/uv.lock" ]]; then
    (cd "$JR_ROOT/backend" && uv run --locked "$@")
  else
    (cd "$JR_ROOT/backend" && uv run "$@")
  fi
}

# Run a Python >= 3.9 interpreter: python3, python, else uv's.
run_py() {
  local c
  for c in python3 python; do
    if have "$c" && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
      "$c" "$@"; return
    fi
  done
  have uv || { warn "no python interpreter and no uv"; return 127; }
  uv run --no-project --quiet --python "$JR_PYTHON_VERSION" python "$@"
}

# Kill a background job and its whole process tree (native Windows children too).
kill_tree() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || return 0
  if is_windows && [[ -r "/proc/$pid/winpid" ]] && have taskkill; then
    taskkill //F //T //PID "$(cat "/proc/$pid/winpid")" >/dev/null 2>&1 || true
  fi
  if have pkill; then pkill -TERM -P "$pid" 2>/dev/null || true; fi
  kill -TERM "$pid" 2>/dev/null || true
}
