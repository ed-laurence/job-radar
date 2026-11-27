# GitHub: private repo, prod environment with a required reviewer, Actions
# variables. Rulesets live in ../github-finalize (applied after the first push
# so they don't block it). The deploy key and webhook secret are created by
# bootstrap.sh outside OpenTofu, so no secret is in state.
#
# Everything here is gated by var.enable_github (ADR-0008). With it false there
# is no GitHub resource or data source, so the provider needs no token.

data "github_user" "reviewer" {
  count = var.enable_github ? 1 : 0

  username = var.github_reviewer == null ? "" : var.github_reviewer

  lifecycle {
    precondition {
      condition     = var.github_reviewer != null && var.github_reviewer != ""
      error_message = "github_reviewer is required when enable_github = true."
    }
  }
}

resource "github_repository" "this" {
  count = var.enable_github ? 1 : 0

  name        = var.github_repo
  description = "Job Radar"
  visibility  = "private"

  has_issues             = true
  has_projects           = false
  has_wiki               = false
  auto_init              = false
  allow_merge_commit     = false
  allow_rebase_merge     = false
  allow_squash_merge     = true
  allow_auto_merge       = false
  delete_branch_on_merge = true
  archive_on_destroy     = true
}

resource "github_repository_environment" "prod" {
  count = var.enable_github ? 1 : 0

  repository          = github_repository.this[0].name
  environment         = "prod"
  can_admins_bypass   = false
  prevent_self_review = false

  reviewers {
    users = [tonumber(data.github_user.reviewer[0].id)]
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "prod_main" {
  count = var.enable_github ? 1 : 0

  repository     = github_repository.this[0].name
  environment    = github_repository_environment.prod[0].environment
  branch_pattern = "main"
}

# Deploy identifiers are scoped to the prod environment, so workflows that
# don't target it (e.g. ci.yml on agent/*) can't read them.
locals {
  prod_env_vars = {
    GCP_PROJECT_ID     = google_project.this.project_id
    GCP_REGION         = var.region
    GCP_WIF_PROVIDER   = google_iam_workload_identity_pool_provider.deploy.name
    GCP_DEPLOY_SA      = google_service_account.deploy.email
    TF_STATE_BUCKET    = google_storage_bucket.state.name
    TF_STATE_KMS_KEY   = module.state_kms.key_ids[var.state_key_name]
    ARTIFACT_REGISTRY  = "${var.region}-docker.pkg.dev/${google_project.this.project_id}/${google_artifact_registry_repository.images.repository_id}"
    GCP_ORG_ENABLED    = var.org_id == null ? "false" : "true"
    GCP_NOTIFY_CHANNEL = google_monitoring_notification_channel.email.id
    GCP_PROJECT_NUMBER = google_project.this.number
  }

  # Read by security-review.yml (no environment). Useless without passing the
  # review provider's job_workflow_ref condition.
  repo_vars = {
    GCP_REVIEW_WIF_PROVIDER = google_iam_workload_identity_pool_provider.review.name
    GCP_REVIEW_SA           = google_service_account.review.email
    GCP_REVIEW_PROJECT_ID   = google_project.this.project_id
  }
}

resource "github_actions_environment_variable" "prod" {
  for_each = var.enable_github ? local.prod_env_vars : {}

  repository    = github_repository.this[0].name
  environment   = github_repository_environment.prod[0].environment
  variable_name = each.key
  value         = each.value
}

resource "github_actions_variable" "repo" {
  for_each = var.enable_github ? local.repo_vars : {}

  repository    = github_repository.this[0].name
  variable_name = each.key
  value         = each.value
}
