# Coding standards (judgement items only; tools enforce the rest)

## Python
- **CODE-01** Type everything. Public functions have full signatures, and `Any` needs a comment explaining why. pyright runs in strict mode.
- **CODE-02** Use Pydantic models for data that crosses a boundary (API, LLM output, queue messages) and `@dataclass(frozen=True)` for internal value objects.
- **CODE-03** Async all the way in `api` and adapters. No blocking I/O inside `async def`.
- **CODE-04** Never write a bare `except Exception`. Catch the specific errors, or re-raise as a typed domain error with context.
- **CODE-05** Use descriptive names: no single letters except for loop indices, no abbreviations like `mgr` or `cfg`. Functions are verbs and booleans are `is_`/`has_`.
- **CODE-06** Functions stay under 40 lines and modules under 400. Split by responsibility, not by line count alone.
- **CODE-07** Time comes from the `Clock` port and ids from the `Ids` port. Never call `datetime.now()` or `uuid4()` directly in domain code.
- **CODE-08** Use structured logging (`structlog`, JSON) with no f-string interpolation of user data into messages. Never log prompts, emails or tokens.

## TypeScript / React
- **CODE-20** `strict: true`. No `any`; use `unknown` + narrowing. Types for API data come only from the generated client.
- **CODE-21** Components are function components with explicit prop types, one component per file, named exports.
- **CODE-22** Server state uses TanStack Query (the generated hooks/wrappers). Local UI state uses `useState`/`useReducer`. No global store unless an ADR approves it.
- **CODE-23** Accessibility: interactive elements are real buttons or links with labels. Drag-and-drop has keyboard support (dnd-kit sensors) and announcements.
- **CODE-24** No inline styles except dynamic values; use design tokens (CSS variables) from `web/src/lib/tokens.css`.
- **CODE-25** Never interpolate user data into HTML (`dangerouslySetInnerHTML` is banned). Markdown from the LLM is rendered with a sanitizing renderer.

## Both
- **CODE-40** Comments explain *why*, not *what*. Remove dead code; don't comment it out.
- **CODE-41** Use obviously fake values in examples and fixtures (`user@example.test`, `Acme Pty Ltd`).
