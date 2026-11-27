"""The coordinator: the only app service sandboxed dev agents reach (besides llm-proxy).

A small internal HTTP API (JSON under `/v1`) plus `GET /healthz` (ADR-0009). Every call
carries a task-scoped token (`tokens`); every operation acts only on that token's task and run,
through the dev-board domain services and the lease/run stores (`service`). Inputs are strict
and capped, oversized messages are refused (never cut), and every response passes the
redaction filter.

Run it with `uv run python -m app.coordinator`; `app.coordinator.server.create_app` is the
factory. Design and schema: `docs/features/coordinator.md`.
"""
