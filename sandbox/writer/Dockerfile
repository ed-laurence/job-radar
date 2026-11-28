# syntax=docker/dockerfile:1.7
# Writer sandbox image: Python 3.12 + uv, Node 22 + pnpm (corepack), git, Postgres 16, the
# Claude Code CLI and the Claude Agent SDK. Backend and web dependencies are baked in from the
# lockfiles, so nothing installs at runtime (the sandbox has no package mirror).
#
# Build from the REPO ROOT (the context needs backend/uv.lock and web/pnpm-lock.yaml):
#   docker build -f sandbox/writer/Dockerfile -t jobradar-sandbox-writer:dev .
# The context is filtered by sandbox/writer/Dockerfile.dockerignore (allowlist).
# Run it only through sandbox/run.sh, which applies the hardening flags.
#
# Base images are pinned by tag and digest. Renovate moves them.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
ARG NODE_IMAGE=node:22.23.5-bookworm-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}
ARG PG_MAJOR=16
ARG PNPM_VERSION=9.15.9
ARG CLAUDE_CODE_VERSION=2.1.332
ARG SANDBOX_UID=10001

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    COREPACK_HOME=/opt/corepack \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0

# --- OS packages: git, Postgres 16 from the PGDG apt repo (key shipped in postgresql-common).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates git postgresql-common; \
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y; \
    mkdir -p /etc/postgresql-common; \
    echo 'create_main_cluster = false' > /etc/postgresql-common/createcluster.conf; \
    apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}" "postgresql-client-${PG_MAJOR}"; \
    apt-get clean; rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/lib/postgresql/${PG_MAJOR}/bin:$PATH

# --- uv and Node 22 (copied from pinned images; no curl | sh).
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN set -eux; \
    ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm; \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx; \
    ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack; \
    corepack enable --install-directory /usr/local/bin pnpm; \
    corepack install -g "pnpm@${PNPM_VERSION}"; \
    pnpm --version; node --version; uv --version; postgres --version

# --- Claude Code CLI (pinned, own prefix) and the Claude Agent SDK (hash-locked venv).
RUN set -eux; \
    npm install -g --prefix /opt/claude-code --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
    ln -s /opt/claude-code/bin/claude /usr/local/bin/claude; \
    npm cache clean --force; rm -rf /root/.npm
COPY sandbox/writer/agent-requirements.txt /opt/agent/requirements.txt
RUN set -eux; \
    uv venv --python /usr/local/bin/python3.12 /opt/agent/venv; \
    uv pip install --python /opt/agent/venv/bin/python --require-hashes --no-deps --compile-bytecode \
      -r /opt/agent/requirements.txt; \
    /opt/agent/venv/bin/python -c "import claude_agent_sdk"; \
    rm -rf /root/.cache

# --- Backend deps from backend/uv.lock. The project is installed editable at the path the
# entrypoint clones to (/workspace/repo/backend), so the real sources resolve at runtime.
ENV UV_PROJECT_ENVIRONMENT=/opt/deps/backend-venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_LINK_MODE=copy
WORKDIR /workspace/repo/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN set -eux; \
    mkdir -p src/app; touch src/app/__init__.py; \
    uv sync --frozen --compile-bytecode --no-cache; \
    /opt/deps/backend-venv/bin/python -c "import fastapi, sqlalchemy, pytest"

# --- Web deps from web/pnpm-lock.yaml (Linux-native optional binaries). The store stays in
# the image so an unchanged lockfile can be re-linked offline.
WORKDIR /opt/deps/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN set -eux; \
    pnpm install --frozen-lockfile --store-dir /opt/deps/pnpm-store --config.confirmModulesPurge=false; \
    ln -s /tmp/vite-cache node_modules/.vite; \
    ln -s /tmp/vite-temp node_modules/.vite-temp; \
    rm -rf /workspace/repo /root/.cache /root/.local

# --- Offline generators for scripts/gen-api.sh, at the versions pinned in scripts/lib/common.sh:
# datamodel-code-generator + ruff (an ephemeral `uv run --with` env) and openapi-typescript (an
# `npx -y` install). Both caches are read-only here; offline-caches.sh copies them under /tmp at
# runtime because uv and npx write to their caches (to /workspace: /tmp is a noexec tmpfs).
COPY scripts/lib/common.sh /opt/deps/gen-versions.sh
RUN set -eux; \
    eval "$(grep -E '^JR_(PYTHON|DATAMODEL_CODEGEN|RUFF|OPENAPI_TS)_VERSION="[0-9.]+"$' /opt/deps/gen-versions.sh)"; \
    test -n "$JR_DATAMODEL_CODEGEN_VERSION" -a -n "$JR_RUFF_VERSION" -a -n "$JR_OPENAPI_TS_VERSION"; \
    UV_CACHE_DIR=/opt/deps/gen-cache/uv uv run --no-project --quiet --python "$JR_PYTHON_VERSION" \
      --with "datamodel-code-generator==${JR_DATAMODEL_CODEGEN_VERSION}" --with "ruff==${JR_RUFF_VERSION}" \
      datamodel-codegen --version; \
    npm_config_cache=/opt/deps/gen-cache/npm npx -y "openapi-typescript@${JR_OPENAPI_TS_VERSION}" --version; \
    rm -rf /opt/deps/gen-cache/npm/_logs /root/.npm /root/.cache; \
    chmod -R a+rX /opt/deps/gen-cache

# --- Runtime user and layout. Only /workspace, /tmp and /out are writable (see sandbox/run.sh).
COPY sandbox/writer/entrypoint.sh sandbox/writer/offline-caches.sh sandbox/writer/handoff.py \
     sandbox/writer/pytest_report.py sandbox/writer/agent_runner.py sandbox/writer/agent_policy.py \
     sandbox/writer/agent_prompt.py sandbox/writer/coordinator_client.py \
     sandbox/writer/coordinator_tools.py sandbox/writer/agent_sdk.py sandbox/writer/llm_usage.py \
     sandbox/writer/run_log.py sandbox/writer/plan_contract.py sandbox/writer/triage_contract.py \
     sandbox/writer/review_contract.py \
     /opt/sandbox/
RUN set -eux; \
    useradd --uid "${SANDBOX_UID}" --user-group --no-create-home --home-dir /tmp/home \
      --shell /usr/sbin/nologin sandbox; \
    mkdir -p /workspace /in /out; \
    chown "${SANDBOX_UID}:${SANDBOX_UID}" /workspace /out; \
    chmod 0755 /opt/sandbox/entrypoint.sh /opt/sandbox/offline-caches.sh; \
    /usr/local/bin/python3 -c "import sys; sys.path.insert(0, '/opt/sandbox'); import handoff, plan_contract, triage_contract, review_contract"; \
    /opt/agent/venv/bin/python -c "import sys; sys.path.insert(0, '/opt/sandbox'); import agent_runner, agent_sdk"

# Offline everything at runtime.
ENV HOME=/tmp/home \
    UV_OFFLINE=1 \
    UV_FROZEN=1 \
    UV_NO_SYNC=1 \
    UV_CACHE_DIR=/workspace/.cache/uv \
    npm_config_cache=/workspace/.cache/npm \
    npm_config_offline=true \
    npm_config_store_dir=/opt/deps/pnpm-store \
    npm_config_update_notifier=false \
    COREPACK_ENABLE_NETWORK=0 \
    COREPACK_ENABLE_AUTO_PIN=0 \
    NEXT_TELEMETRY_DISABLED=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/deps/backend-venv/bin:$PATH

USER ${SANDBOX_UID}:${SANDBOX_UID}
WORKDIR /workspace
ENTRYPOINT ["/opt/sandbox/entrypoint.sh"]
