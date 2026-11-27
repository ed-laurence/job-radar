# Testing standards

## Principles
- **TEST-01** Write the test first. A change that adds behaviour adds a test that fails without the change.
- **TEST-02** Every bug fix adds a regression test named after the bug.
- **TEST-03** Never weaken, skip or delete an existing test to make a change pass. Changing an existing test needs a human `modify-tests:` label (dev-board agents) or explicit approval (humans).
- **TEST-04** Tests are hermetic. No network: `conftest.py` blocks all non-loopback sockets. No real LLM calls: use `LLM=replay` fixtures or fakes. No wall-clock time: use the `Clock` fake.
- **TEST-05** Assert on behaviour, not on implementation details or log text.

## Where tests go
| Kind | Location | Tooling | Notes |
|---|---|---|---|
| Unit | `backend/tests/unit/` | pytest | Domain logic against fakes. Fast (< 30 s total) |
| Property | `backend/tests/property/` | Hypothesis | Invariants: ranking order, state machine transitions, lease overlap, email allowlist, redaction |
| Contract | `backend/tests/contract/` | Schemathesis + pytest | The API conforms to `api/openapi.yaml`; coordinator API schemas; message size caps |
| Integration | `backend/tests/integration/` | pytest + real Postgres | Row-level security, migrations, LISTEN/NOTIFY, repositories. Marked `@pytest.mark.integration`; needs `DATABASE_URL` |
| Evals | `backend/tests/evals/` | pytest + labelled sets | Run nightly or on change, not in the fast suite |
| Web unit | `web/src/**/*.test.tsx` | Vitest + Testing Library | Components and hooks; API mocked with MSW from the spec |
| E2E | `web/tests/e2e/` | Playwright | Critical journeys only, against `scripts/dev.sh` offline mode |

## Postgres for integration tests
`DATABASE_URL` points at a disposable database. `scripts/dev-db.sh` starts one: Docker if it's available, otherwise an embedded Postgres (`pgserver`). If neither is available the integration tests are **skipped with an explicit reason**, and the handoff names that skip. They never silently pass.

## LLM replay
- `backend/tests/fixtures/llm/` holds recorded responses keyed by `(feature, prompt_hash)`.
- Replay fails loudly on an unknown hash.
- Re-record only with `LLM=record` (human-run) when prompts change.

## Coverage and mutation
- Line coverage is reported but isn't a target.
- Mutation testing (mutmut / Stryker) on changed files is the quality signal, run weekly and on agent PRs.
