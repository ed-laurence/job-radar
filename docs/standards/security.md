# Security standards

- **SEC-01** Every route except `GET /healthz` and the sign-in flow requires an authenticated user. The API verifies the Identity Platform JWT, `email_verified`, and an **exact** match against the `APP_USERS` allowlist on **every request**.
- **SEC-02** Role checks: `maintainer` is required for triggering dev agents, `/docs`, `/openapi.json` and admin views. `user` covers everything else.
- **SEC-03** Data access always runs under row-level security with `app.user_id` set. No query bypasses row-level security except migrations and the `SECURITY DEFINER` tester reset.
- **SEC-04** Untrusted input (ads, uploads, task text, tool and web results) passes `ContentGuard.inspect_input` before reaching an LLM. LLM output shown to users passes `ContentGuard.inspect_output`. **Fail closed.**
- **SEC-05** PII is de-identified through the `PII` port before prompts. Stored prompts and logs are redacted.
- **SEC-06** Every user-facing payload (JSON, SSE, coordinator results, PR text) passes `app.guards.redaction`: secret patterns, then the runtime denylist, then PII.
- **SEC-07** Errors are RFC 9457 with a correlation id only. `/healthz` returns `{"status":"ok"}` only.
- **SEC-08** Rate limits on every authenticated route, stricter on AI and agent routes. Body size limits on uploads.
- **SEC-09** No secrets in code, fixtures, logs, the client bundle or tofu state. Secrets come from Secret Manager or env at runtime.
- **SEC-10** Outbound HTTP only through adapters, with an allowlist of hosts. No user-controlled URLs are fetched server-side, so there's no SSRF.
- **SEC-11** Dependencies are pinned by lockfile. Adding a dependency is a human decision; dev-board agents can't do it.
