# Workload Identity Federation for GitHub Actions. No service-account keys.
#
# Two pools (one provider each), so a principalSet binding in one pool can
# never match tokens accepted by the other:
# - deploy: only refs/heads/main AND environment prod. Applies prod.
# - review: only security-review.yml on main (job_workflow_ref), triggered by
#   workflow_run. Its service account can only call Vertex.
# Workflows running agent/* branch code match neither condition.

# The owner's numeric id pins the condition to this account even if the login
# is renamed and re-registered by someone else. In the GCP-only stage
# (enable_github = false) it comes from var.github_owner_id, so the providers
# exist before the repo does; the full run looks it up and checks it matches.
data "github_user" "owner" {
  count = var.enable_github ? 1 : 0

  username = var.github_owner
}

locals {
  github_issuer   = "https://token.actions.githubusercontent.com"
  repo_full       = "${var.github_owner}/${var.github_repo}"
  looked_up_owner = var.enable_github ? data.github_user.owner[0].id : null
  owner_id        = var.github_owner_id != null ? var.github_owner_id : local.looked_up_owner

  # Null owner id (GCP-only without github_owner_id) or a mismatch with GitHub.
  owner_id_ok = local.owner_id != null && (local.looked_up_owner == null || local.looked_up_owner == local.owner_id)
  # Interpolated into the conditions; never empty in a plan that passes the preconditions.
  owner_id_str = local.owner_id == null ? "" : local.owner_id
  owner_id_msg = "github_owner_id is required when enable_github = false, and must equal the id GitHub reports for github_owner when enable_github = true."

  # Only claims every GitHub token carries are mapped; environment is checked
  # in the deploy condition only (a missing claim fails the condition).
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.owner_id"      = "assertion.repository_owner_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.workflow_ref"  = "assertion.job_workflow_ref"
    "attribute.event_name"    = "assertion.event_name"
  }

  deploy_condition = join(" && ", [
    "assertion.repository == '${local.repo_full}'",
    "assertion.repository_owner_id == '${local.owner_id_str}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.environment == 'prod'",
  ])

  review_condition = join(" && ", [
    "assertion.repository == '${local.repo_full}'",
    "assertion.repository_owner_id == '${local.owner_id_str}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.event_name == 'workflow_run'",
    "assertion.job_workflow_ref == '${local.repo_full}/.github/workflows/${var.security_review_workflow}@refs/heads/main'",
  ])
}

# --- deploy ---

resource "google_iam_workload_identity_pool" "deploy" {
  project                   = google_project.this.project_id
  workload_identity_pool_id = "github-deploy"
  display_name              = "GitHub deploy"
  description               = "main + environment prod only."

  depends_on = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "deploy" {
  project                            = google_project.this.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.deploy.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC (deploy)"
  attribute_mapping                  = local.attribute_mapping
  attribute_condition                = local.deploy_condition

  oidc {
    issuer_uri = local.github_issuer
  }

  lifecycle {
    precondition {
      condition     = local.owner_id_ok
      error_message = local.owner_id_msg
    }
  }
}

resource "google_service_account" "deploy" {
  project      = google_project.this.project_id
  account_id   = "gh-deploy" # keep in sync with local.deploy_member
  display_name = "GitHub deploy (tofu apply, image build)"

  depends_on = [google_project_service.this]
}

resource "google_service_account_iam_member" "deploy_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.deploy.name}/attribute.repository/${local.repo_full}"
}

resource "google_project_iam_member" "deploy" {
  for_each = toset(var.deploy_sa_roles)

  project = google_project.this.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# With an Organization, prod creates a project-scoped VPC-SC access policy.
resource "google_organization_iam_member" "deploy_vpcsc" {
  count = var.org_id == null ? 0 : 1

  org_id = var.org_id
  role   = "roles/accesscontextmanager.policyAdmin"
  member = "serviceAccount:${google_service_account.deploy.email}"
}

# --- review (Vertex only) ---

resource "google_iam_workload_identity_pool" "review" {
  project                   = google_project.this.project_id
  workload_identity_pool_id = "github-review"
  display_name              = "GitHub security review"
  description               = "security-review.yml on main only."

  depends_on = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "review" {
  project                            = google_project.this.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.review.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC (review)"
  attribute_mapping                  = local.attribute_mapping
  attribute_condition                = local.review_condition

  oidc {
    issuer_uri = local.github_issuer
  }

  lifecycle {
    precondition {
      condition     = local.owner_id_ok
      error_message = local.owner_id_msg
    }
  }
}

resource "google_service_account" "review" {
  project      = google_project.this.project_id
  account_id   = "gh-security-review"
  display_name = "GitHub PR security review (Vertex only)"

  depends_on = [google_project_service.this]
}

resource "google_service_account_iam_member" "review_wif" {
  service_account_id = google_service_account.review.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.review.name}/attribute.repository/${local.repo_full}"
}

resource "google_project_iam_member" "review_vertex" {
  project = google_project.this.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.review.email}"
}
