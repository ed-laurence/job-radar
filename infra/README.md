# infra

OpenTofu for the GCP footprint (prod only). The HCL is Terraform-compatible except
for the `encryption.tofu` files, which only OpenTofu reads.

## Layout

| Path | What |
|---|---|
| `bootstrap/` | Run by the owner (`scripts/bootstrap.sh`), in two stages (ADR-0008): project under an optional Organization, billing, APIs, state bucket + KMS state key, two WIF pools (deploy, review), Artifact Registry, budget; and, only with `enable_github = true`, on GitHub the private repo, the `prod` environment and Actions variables. |
| `github-finalize/` | Repository rulesets on `main`. Applied **after** the first push (`deploy.yml` last step or `bootstrap.sh --finalize`). |
| `envs/prod/` | Everything else, applied by `deploy.yml` through the deploy WIF provider. |
| `modules/` | `network`, `vpc_sc`, `cloudsql`, `run_service`, `run_job`, `scheduler`, `identity_platform`, `model_armor`, `sdp`, `monitoring`, `secrets`, `kms`. |
| `policy/` | Conftest (Rego) policies run against `tofu show -json` plan output. |
| `tests/` | `*.tftest.hcl` with mock providers; plans each root and module with fake inputs. |

## Staged bootstrap (ADR-0008)

| Stage | Command | `enable_github` | Adds |
|---|---|---|---|
| GCP-only (now) | `scripts/bootstrap.sh --gcp-only --i-understand-this-creates-gcp-resources` | `false` | Everything GCP in `bootstrap/` (WIF conditions name the future repo via `github_owner`, `github_repo`, `github_owner_id`), Secret Manager `app-users` + `denylist`, Vertex checks. No `gh`, no GitHub token. |
| Full (initial commit) | `scripts/bootstrap.sh --i-understand-this-creates-gcp-resources` | `true` | GitHub repo, `prod` environment, Actions variables, deploy key, webhook HMAC, Actions secrets. Idempotent on top of stage 1. |
| Finalize (after first deploy) | `scripts/bootstrap.sh --finalize --i-understand-this-creates-gcp-resources` | n/a | `github-finalize/` rulesets, webhook. |

With `enable_github = false` no GitHub resource or data source is planned and the provider has no owner, so it is configured anonymously and needs no `GITHUB_TOKEN`. Once the state holds GitHub resources, `--gcp-only` refuses to run (it would plan their removal). OpenTofu is required; the script refuses to fall back to Terraform (`winget install OpenTofu.Tofu`).

## How the roots relate

1. `bootstrap/` starts with local state. Pass 1 runs with `encryption.tofu` moved aside, because the KMS key doesn't exist yet. Pass 2 writes an untracked `backend_override.tf` (gcs, prefix `bootstrap`), restores the file and runs `tofu init -migrate-state`. The details are in `bootstrap/encryption.tofu`.
2. Its outputs (project number, deploy/review SAs, state key, notification channel) go into the untracked `~/.config/jobradar/prod.tfvars` and into the GitHub `prod` environment variables.
3. `envs/prod/` uses `backend "gcs"` with prefix `prod`, and its state is encrypted from the first apply.
4. `github-finalize/` uses prefix `github-finalize` and runs once `main` exists on GitHub.

No secret values are in state. Secret Manager holds containers only. The deploy key, webhook HMAC, `APP_USERS` and the denylist are written by `bootstrap.sh` through `gcloud` and `gh` (the deploy key and HMAC only in the full run).

## Validate (read-only)

```sh
terraform fmt -check -recursive infra                     # or: tofu fmt -check -recursive infra
for d in infra/bootstrap infra/github-finalize infra/envs/prod infra/modules/*; do
  (cd "$d" && terraform init -backend=false -input=false >/dev/null && terraform validate)
done
(cd infra/tests && terraform init -input=false >/dev/null && terraform test)   # both bootstrap modes
tofu plan -out=plan.bin && tofu show -json plan.bin > plan.json   # in CI, with credentials
conftest test --policy infra/policy plan.json
conftest verify --policy infra/policy
```

Never run `apply`, `destroy` or `import` locally. Only `deploy.yml` applies prod, after the environment approval.

## Contracts with other parts of the repo

- **Logging:** `modules/monitoring/main.tf` lists the JSON log fields that the log-based metrics read (`event=llm_call`, `guard_block`, and so on).
- **Sandbox jobs** get no identifiers or secrets in their environment. The API passes per-execution paths as job overrides.
- **`api` is IAM-protected.** Its invokers are `web`, the red-team agent and the uptime checker. Only `web` is public.
