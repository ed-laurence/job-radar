# ADR-0008: Staged bootstrap (GCP-only now, GitHub at the initial commit)

- Status: accepted
- Date: 2026-11-27

## Context
The owner needs Vertex access for local agent development now. The rule is that nothing touches GitHub before the initial commit. The old `bootstrap.sh` did GCP and GitHub in one pass: `gh` login, GitHub provider resources, the deploy key and HMAC, and the Actions secrets.

## Decision
- `bootstrap.sh --gcp-only` runs the tool checks (no `gh`), the gcloud + ADC sign-in, Organization detection, the billing pick and the untracked tfvars. It then applies `infra/bootstrap` with `enable_github = false` (both state passes, KMS encryption), writes the `app-users` and `denylist` secrets, and runs the Vertex checks.
- In `infra/bootstrap`, every GitHub resource and data source uses `count`/`for_each` on `enable_github`. When it's off, the provider has no owner and is configured anonymously, so it needs no token.
- The WIF providers are created in stage 1. Their conditions name the future repo through `github_owner`/`github_repo` and pin `github_owner_id`, which is read anonymously from the public GitHub API or typed in. The full run looks the id up through the provider, and a precondition fails if the two differ, so the conditions never change between stages.
- The plain full run later adds the GitHub parts on top of stage 1, idempotently. `--finalize` is unchanged.
- `--gcp-only` refuses to run once the state holds GitHub resources, because it would plan their removal.
- OpenTofu (>= 1.8) is mandatory. There is no Terraform fallback, because Terraform ignores `encryption.tofu` and would write plaintext state.

## Consequences
- The GCP-only footprint costs about US$0.10 a month when idle: the KMS key version, plus storage that stays within the free tiers. Vertex is billed per token.
- WIF trusts a repository that doesn't exist yet. Only the owner (matched by numeric id) can create it, and `main` + `prod` are still required. Until then, no token can match.
- One extra read-only, unauthenticated call to `api.github.com` in stage 1.
- `enable_github` defaults to `false`, so a full run must set it. `bootstrap.sh` writes it explicitly.
