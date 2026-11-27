#!/usr/bin/env bash
# Runs INSIDE the writer image (isolation.sh, via sandbox/run.sh --entrypoint /bin/bash):
# scripts/gen-api.sh must work offline with only what the image bakes in. The input bundle
# holds api/, scripts/ and the generated dirs from the working tree; egress is blocked.
set -euo pipefail
mkdir -p "$HOME"
export GIT_CONFIG_GLOBAL="$HOME/.gitconfig" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
git config --global safe.directory '*'
git clone --quiet /in/input.bundle /workspace/repo
cd /workspace/repo
/opt/sandbox/offline-caches.sh
scripts/gen-api.sh >/tmp/gen-api.log 2>&1 || { tail -n 15 /tmp/gen-api.log >&2; exit 1; }
for f in backend/src/app/generated/models.py web/src/lib/api/generated/schema.d.ts \
  web/src/lib/api/generated/client.ts web/src/lib/api/generated/labels.ts; do
  [[ -s "$f" ]] || { echo "missing $f" >&2; exit 1; }
done
# Deterministic: a second, --check run finds the output in sync.
scripts/gen-api.sh --check >/tmp/gen-api-check.log 2>&1 || { tail -n 15 /tmp/gen-api-check.log >&2; exit 1; }
echo "gen-api offline: ok"
