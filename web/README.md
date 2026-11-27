# web

Next.js 16 (App Router, TypeScript strict) front end and backend-for-frontend for Job Radar.

## Package manager

pnpm 9.15.9 through **corepack** (pnpm is not installed globally): `corepack pnpm@9.15.9 <cmd>`.
`packageManager` in `package.json` pins it, and `scripts/lib/common.sh` picks the same launcher.
Set `COREPACK_ENABLE_DOWNLOAD_PROMPT=0` to skip corepack's prompt. Node ≥ 20.10. Several tool
versions are held back for Node 20.10: vitest 4, vite 6, jsdom 26, msw 2, dependency-cruiser 16, firebase 12.

| Script            | What                                                                                           |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| `dev`             | `next dev` on 127.0.0.1:3000 (scripts/dev.sh runs this)                                        |
| `build` / `start` | production build / server                                                                      |
| `test`            | Vitest (jsdom, Testing Library, MSW)                                                           |
| `lint`            | Biome lint + format check (`biome ci .` in check.sh)                                           |
| `typecheck`       | `tsc --noEmit`                                                                                 |
| `depcruise`       | layering: `app → features → components → lib`, no cross-feature imports                        |
| `e2e`             | Playwright against the running app (`E2E_BASE_URL`); skips with a reason if web or api is down |
| `gen-api`         | `scripts/gen-api.sh` regenerates `src/lib/api/generated/` incl. `labels.ts` (never hand-edit)  |

## Layout

- `src/app/`: routes only. `(app)/` is the signed-in area behind `AppFrame` (guard + shell).
  `api/[...path]` and `hooks/github` are the BFF route handlers.
- `src/features/<name>/`: data and state per feature. `session/` holds sign-in, the guards and the frame.
  The other folders are placeholders for their owners.
- `src/components/`: presentational only (shell, states, sanitizing `Markdown`, `ConfirmPanel`,
  `StepList`, `ResearchForm`/`ResearchProgress`, `chartTheme.ts` for Recharts).
- `src/lib/`: `api/` (typed client, problem+json errors and `describeError`, 409 helpers, TanStack
  Query provider, generated enum labels via `enumLabels`/`labelFor`, server-only `bff.ts`),
  `session/` (`useMe`/`useRole`, one `/me` cache key), `research/` (research-run hooks shared by
  roles and targets), `sse.ts`, `analytics.ts`, `auth/`, `csp.ts`, `tokens.css` (incl. `--color-chart-*`).
- `src/lib/testing/`: test-only render helpers, MSW and SSE fixtures. dependency-cruiser keeps it
  out of production code (`no-test-code-in-app`); feature `testing.ts` files hold only fixtures.

## Environment

See `.env.example`. Only `NEXT_PUBLIC_*` values reach the browser.

- `NEXT_PUBLIC_AUTH_MODE=fake|identity` (or the backend's `AUTH=fake`). Unset means fake in dev/test
  and identity in production builds. Fake mode offers `owner@example.test` and `tester@example.test`
  and sends `Authorization: Bearer fake:<email>`.
- Identity mode needs only `NEXT_PUBLIC_FIREBASE_API_KEY` (+ optional `NEXT_PUBLIC_FIREBASE_TENANT_ID`).
- BFF (server side): `API_INTERNAL_URL` (falls back to dev.sh's `API_BASE_URL`, then `http://127.0.0.1:8000`).
  If `API_ID_TOKEN_AUDIENCE` is set, a Google ID token from the metadata server is sent as
  `X-Serverless-Authorization`, leaving the user's `Authorization` header in place.

## Security

- CSP per request with a nonce (`src/proxy.ts`), `frame-ancestors 'none'`, `connect-src 'self'`
  (plus the two Identity Platform endpoints in identity mode). HSTS and other headers are set in `next.config.ts`.
- `productionBrowserSourceMaps: false`. `dangerouslySetInnerHTML` is banned by Biome, and raw `fetch`
  outside `src/lib/api` and `src/lib/sse.ts` is banned too.
