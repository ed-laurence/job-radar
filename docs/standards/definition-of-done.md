# Definition of done

A change is done only when **all** of these hold:

1. **Spec:** HTTP changes are in `api/openapi.yaml` first, and Spectral passes. The generated code is regenerated and in sync.
2. **Tests:** new behaviour has failing-first tests at the right level (`testing.md`). The fast suite is green, and integration tests are green or skipped with the reason stated.
3. **Gates:** `scripts/check.sh` is green, or each skipped gate is named with the missing tool.
4. **Layering:** import-linter and dependency-cruiser pass. No new cross-feature imports.
5. **Security:** new routes have authz (an authenticated user plus a role where needed). Outputs shown to users pass the redaction filter. No secrets, identifiers or PII were added (gitleaks + the denylist scan).
6. **Docs:** user-visible behaviour is described in `docs/features/`. A decision that changes architecture gets an ADR in `docs/adr/`. A change to services, modules, flows, runtime modes, jobs, infra or milestone status updates `docs/architecture.md` in the same change (agents can't edit it, so they name it in `open_questions` instead). New files under `docs/` use lowercase kebab-case names.
7. **Handoff:** a short handoff with `rubric_checked` IDs and any skipped checks.
