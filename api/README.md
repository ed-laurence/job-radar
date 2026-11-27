# API contract

`openapi.yaml` (OpenAPI 3.1) is the source of truth for the HTTP API. Code follows the spec, never the other way round.

## Workflow
1. Edit `api/openapi.yaml`. It is a hotspot: dev-board agents need the hotspot lock to touch it.
2. Lint: `npx -y @stoplight/spectral-cli lint api/openapi.yaml --ruleset api/.spectral.yaml` (zero errors).
3. Check for breaking changes: `oasdiff breaking <base>/api/openapi.yaml api/openapi.yaml --fail-on ERR`.
4. Regenerate: `scripts/gen-api.sh` (backend `generated/` models, web `lib/api/generated/` client). Never hand-edit generated code.
5. Write failing contract tests (`backend/tests/contract/`), then implement.

## Conventions the linter enforces
- `operationId` on every operation (it names the generated functions).
- Every operation documents a 4xx response, and every 4xx/5xx is `application/problem+json` (RFC 9457, with `correlation_id`).
- `maxLength` on every string, `additionalProperties: false` on request bodies.
- Examples use obviously fake data only (`example.test`, Acme Pty Ltd).

Other conventions (in `info.description`): no nulls (absent means unknown), `version` on every write (409 on mismatch), fractional `rank` strings, cursor pagination, `x-required-role: maintainer`, `x-rate-limit-tier`, typed SSE events.

## Versioning
- `info.version` is semver. Additive changes bump the minor; fixes to descriptions bump the patch.
- The URL prefix (`/api`) only changes for a new major version, served alongside the old one until the web client has moved.

## Breaking-change policy
`oasdiff breaking` runs in `scripts/check.sh` and CI against the base branch. Any `ERR`-level change fails the gate. That includes removing or renaming a path, operation, field or enum value, making an optional field required, narrowing a type or limit, and changing an `operationId`.

A breaking change needs a human decision recorded in an ADR, a major version bump, and a migration window. Agents can't approve one. Adding an enum value to a response counts as breaking for strict clients, so do it only with the web client updated in the same change.
