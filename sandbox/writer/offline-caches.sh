#!/usr/bin/env bash
# Seed the writable caches scripts/gen-api.sh needs offline (the root filesystem is read-only).
#
# The image bakes the pinned generators (datamodel-code-generator + ruff for uv, and
# openapi-typescript for npx; versions from scripts/lib/common.sh) into /opt/deps/gen-cache at
# build time. uv and npx both write to their caches, so the entrypoint copies them to
# $UV_CACHE_DIR and $npm_config_cache before the agent starts. Both live under /workspace (the
# exec tmpfs, outside the repo): /tmp is noexec, so the generator binaries can't run there.
# Nothing is fetched.
set -euo pipefail
SRC=/opt/deps/gen-cache
[[ -d "$SRC/uv" && -d "$SRC/npm" ]] || { echo "[sandbox] no baked generator caches" >&2; exit 1; }
mkdir -p "${UV_CACHE_DIR:?}" "${npm_config_cache:?}"
cp -a --no-preserve=ownership "$SRC/uv/." "$UV_CACHE_DIR/"
cp -a --no-preserve=ownership "$SRC/npm/." "$npm_config_cache/"
