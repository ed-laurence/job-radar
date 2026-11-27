#!/usr/bin/env bash
# Start a disposable local Postgres 16 and print its DATABASE_URL.
#
#   scripts/dev-db.sh            start (idempotent) and print DATABASE_URL=<url> on stdout
#   scripts/dev-db.sh --stop     stop it (data kept)
#   scripts/dev-db.sh --reset    stop, delete all data, start fresh
#   scripts/dev-db.sh --status   exit 0 if running
#
#   eval "export $(scripts/dev-db.sh)"     # then: cd backend && uv run pytest -m integration
#
# Engine: Docker (docker-compose.yml service "postgres") when the daemon is reachable,
# otherwise an embedded Postgres via `uv run --with pgserver` (data in ~/.cache/jobradar/pgdata).
# Force one with DEV_DB=docker|pgserver. Only stdout carries the URL; logs go to stderr.
# Credentials here are local-only throwaway values.
set -euo pipefail
JR_SCRIPT=dev-db
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

ACTION=start
for arg in "$@"; do
  case "$arg" in
    --stop) ACTION=stop ;;
    --reset) ACTION=reset ;;
    --status) ACTION=status ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

DB_NAME="${DEV_DB_NAME:-jobradar}"
PGDATA_DIR="$JR_CACHE_DIR/pgdata"
COMPOSE=(docker compose -f "$JR_ROOT/docker-compose.yml")
DOCKER_URL="postgresql://jobradar:jobradar-local@127.0.0.1:55432/${DB_NAME}"

engine="${DEV_DB:-auto}"
if [[ "$engine" == auto ]]; then
  if docker_ok; then engine=docker; else engine=pgserver; fi
fi

# ---------------------------------------------------------------- docker
docker_start() {
  "${COMPOSE[@]}" up -d --wait postgres >&2
  echo "$DOCKER_URL"
}
docker_stop()  { "${COMPOSE[@]}" stop postgres >&2; }
docker_reset() { "${COMPOSE[@]}" rm -s -f -v postgres >&2; docker volume rm -f jobradar_pgdata >/dev/null 2>&1 || true; }
docker_status() { [[ -n "$("${COMPOSE[@]}" ps -q --status running postgres 2>/dev/null)" ]]; }

# ---------------------------------------------------------------- pgserver
pg_ctl_py() {
  have uv || die "neither Docker nor uv is available; install Docker Desktop or uv (https://docs.astral.sh/uv/)"
  uv run --no-project --quiet --python "$JR_PYTHON_VERSION" --with "pgserver==${JR_PGSERVER_VERSION}" \
    python "$(native_path "$JR_ROOT/scripts/lib/pgserver_ctl.py")" "$@"
}
pgserver_start() {
  mkdir -p "$JR_CACHE_DIR"
  local url
  if ! url="$(pg_ctl_py start "$(native_path "$PGDATA_DIR")" "$DB_NAME")"; then
    die "embedded Postgres (pgserver ${JR_PGSERVER_VERSION}) failed to start on this machine. Install Docker, or set DATABASE_URL to a Postgres 16 you control. Logs: $PGDATA_DIR/log"
  fi
  [[ -n "$url" ]] || die "pgserver started but returned no URL"
  echo "$url"
}
pgserver_stop()   { pg_ctl_py stop "$(native_path "$PGDATA_DIR")"; }
pgserver_status() { pg_ctl_py status "$(native_path "$PGDATA_DIR")"; }
pgserver_reset()  { pgserver_stop || true; rm -rf "$PGDATA_DIR"; }

case "$engine" in docker|pgserver) ;; *) die "DEV_DB must be docker, pgserver or auto (got: $engine)" ;; esac
log "engine: $engine"
case "$ACTION" in
  start)
    url="$("${engine}_start")"
    printf 'DATABASE_URL=%s\n' "$url"
    ;;
  stop)   "${engine}_stop" ;;
  status) "${engine}_status" ;;
  reset)
    "${engine}_reset"
    log "data deleted; starting fresh"
    url="$("${engine}_start")"
    printf 'DATABASE_URL=%s\n' "$url"
    ;;
esac
