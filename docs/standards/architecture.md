# Architecture standards

## Backend layers (enforced by import-linter; see `backend/pyproject.toml` `[tool.importlinter]`)
```
app.api  ──►  app.domain  ──►  app.domain.ports (interfaces)
   │                                  ▲
   └──► app.adapters ─────────────────┘   (adapters implement ports)
app.main wires adapters into domain via dependency injection
```
- **ARCH-01** `app.domain` imports only `app.domain.*`, the stdlib, and `pydantic`. No FastAPI, SQLAlchemy, httpx, anthropic or google libraries.
- **ARCH-02** External systems (DB, LLM, Model Armor, SDP, GCS, GitHub, scrapers, clock, id generation) are reached only through a **port** (a `Protocol` in `app.domain.ports`) and implemented in `app.adapters.*`.
- **ARCH-03** `app.api` routes are thin. They validate (generated models), call one domain service and map the result or error to HTTP. They contain no business rules.
- **ARCH-04** Every port has a **fake** in `app.adapters.fakes` that the tests and offline dev (`LLM=replay`, `GUARDS=fake`) use.
- **ARCH-05** Multi-tenancy: every user-owned table has a `user_id` column with **Postgres row-level security**. Request handlers set `app.user_id` per transaction. Domain services never take a raw `user_id` from the client body.
- **ARCH-06** Configuration comes from env vars via one `Settings` object (pydantic-settings), read once at startup. Nothing else reads env vars.

## Error model
- **ARCH-10** Domain raises typed errors (`NotFound`, `Conflict`, `Forbidden`, `ValidationFailed`, `GuardBlocked`, `BudgetExceeded`). `app.api` maps them to **RFC 9457** problem responses. No stack traces, SQL or internal hostnames in any response.
- **ARCH-11** Optimistic concurrency: mutable resources carry a `version` integer. Updates send `If-Match`/`version`; a mismatch returns 409.

## AI calls
- **ARCH-20** All LLM calls go through `app.adapters.llm` (the port is `domain.ports.LLM`). It handles feature routing from `ai/models.yaml`, Model Armor (in/out), SDP de-identification, `llm_calls` logging, budgets and the fallback.
- **ARCH-21** LLM structured output is always validated with Pydantic. On a validation failure: one retry, then the fallback model, then `ValidationFailed`.
- **ARCH-22** Untrusted text is wrapped in clearly delimited data blocks and never concatenated into the system prompt.

## Streaming
- **ARCH-30** SSE endpoints emit typed events (`segment`, `retract`, `step`, `done`, `error`). Every emitted payload passes the redaction filter (`app.guards.redaction`).
- **ARCH-31** There's one `LISTEN` connection per process, fanned out in memory. Handlers never open their own `LISTEN` connections.

## Web layers (enforced by dependency-cruiser)
```
app (routes/pages) ──► features/* ──► components/* ──► lib/*
```
- **ARCH-40** `components/` are presentational: props in, events out, no data fetching.
- **ARCH-41** `features/<name>/` own data fetching (generated client + TanStack Query), state and composition. Features never import each other; shared code moves to `lib/`.
- **ARCH-42** The API is accessed only through `lib/api` (a wrapper around the generated client). No raw `fetch` to the API anywhere else.
- **ARCH-43** No secrets or non-public config in the client bundle. Only `NEXT_PUBLIC_*` variables that are public by design.
