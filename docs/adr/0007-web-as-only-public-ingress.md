# ADR 0007: `web` is the only public ingress (BFF proxy)

Status: accepted

## Context
Without a load balancer (ADR-0005), each Cloud Run service is either public or IAM-only. A public `api` would expose the whole HTTP surface directly. GitHub webhooks also need a public entry point.

## Decision
- `web` is the only service that allows `allUsers`.
- `api` and `coordinator` are IAM-only.
- `web` runs a same-origin proxy (`src/app/api/[...path]/route.ts`) for `/api/*` and `/hooks/github`:
  - It forwards an allowlist of headers and passes SSE through unbuffered.
  - In prod it attaches a Google ID token for the `api` audience.
  - Webhook bodies are passed through raw, so the backend can verify the HMAC.
- The backend still authenticates every user request with the Identity Platform JWT (SEC-01); the ID token only proves the call came from `web`.

## Consequences
- One public surface to harden (CSP `connect-src 'self'`, rate limits).
- A small latency cost from the extra hop.
- The browser never learns the `api` URL.

M0 spike items carried from infra:
- the uptime-checker service-agent principal;
- Scheduler and uptime checks inside the VPC-SC perimeter;
- whether the Identity Platform blocking function must allow unauthenticated invocation (it's deployed by CI, not tofu).
