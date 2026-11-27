# syntax=docker/dockerfile:1.7
# Coordinator: the internal HTTP API sandboxed dev agents call (http://coordinator:8100).
# Build from backend/ (compose: `docker compose --profile agents build coordinator`):
#   docker build -f coordinator.Dockerfile -t jobradar-coordinator:dev .
# The context is filtered by coordinator.Dockerfile.dockerignore (allowlist). Dependencies come
# from uv.lock (frozen, no dev group); the runtime stage has no uv, no compiler and no network
# needs. Base images are pinned by tag and digest (the same ones as sandbox/); Renovate moves them.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    COORDINATOR_HOST=0.0.0.0 \
    COORDINATOR_PORT=8100
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
RUN useradd --uid 10003 --no-create-home --shell /usr/sbin/nologin coordinator
USER 10003:10003
EXPOSE 8100
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2)"]
# The container form of `uv run python -m app.coordinator` (the venv is already synced).
ENTRYPOINT ["python", "-m", "app.coordinator"]
