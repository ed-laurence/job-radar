# Bootstrap invariants: WIF scoping, Vertex-only review identity, private
# repo with a prod reviewer. Mock providers only. Both modes are covered:
# enable_github = true (full run) and the default GCP-only stage (ADR-0008).

mock_provider "google" {}
mock_provider "google" {
  alias = "billing"
}
mock_provider "github" {
  mock_data "github_user" {
    defaults = {
      id = "1000"
    }
  }
}

variables {
  project_id        = "proj-xxxx-0000"
  project_name      = "example project"
  billing_account   = "000000-000000-000000"
  state_bucket_name = "proj-xxxx-0000-tofu-state"
  alert_email       = "alerts@example.test"
  github_owner      = "example-owner"
  github_repo       = "example-repo"
  github_reviewer   = "example-owner"
}

run "bootstrap_without_org" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    enable_github = true
  }

  assert {
    condition     = google_project.this.org_id == null && length(google_organization_iam_member.deploy_vpcsc) == 0
    error_message = "Without an Organization the project has no parent and no org-level grant."
  }

  assert {
    condition     = google_project.this.deletion_policy == "PREVENT" && google_project.this.auto_create_network == false
    error_message = "Project must be deletion-protected with no default network."
  }

  assert {
    condition = alltrue([
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.ref == 'refs/heads/main'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.environment == 'prod'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.repository == 'example-owner/example-repo'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.repository_owner_id == '1000'"),
    ])
    error_message = "Deploy provider must require main + environment prod for this repo."
  }

  assert {
    condition = alltrue([
      strcontains(google_iam_workload_identity_pool_provider.review.attribute_condition, "assertion.job_workflow_ref == 'example-owner/example-repo/.github/workflows/security-review.yml@refs/heads/main'"),
      strcontains(google_iam_workload_identity_pool_provider.review.attribute_condition, "assertion.event_name == 'workflow_run'"),
    ])
    error_message = "Review provider must be bound to security-review.yml on main via job_workflow_ref."
  }

  assert {
    condition     = google_iam_workload_identity_pool.deploy.workload_identity_pool_id != google_iam_workload_identity_pool.review.workload_identity_pool_id
    error_message = "Deploy and review must use separate pools."
  }

  assert {
    condition     = google_project_iam_member.review_vertex.role == "roles/aiplatform.user"
    error_message = "Review identity must be Vertex-only."
  }

  assert {
    condition     = google_storage_bucket.state.public_access_prevention == "enforced" && google_storage_bucket.state.versioning[0].enabled
    error_message = "State bucket must be private and versioned."
  }

  assert {
    condition     = google_artifact_registry_repository.images.format == "DOCKER"
    error_message = "Artifact Registry holds images only."
  }

  assert {
    condition     = github_repository.this[0].visibility == "private"
    error_message = "Repository must be private."
  }

  assert {
    condition     = github_repository_environment.prod[0].reviewers[0].users == toset([1000]) && github_repository_environment_deployment_policy.prod_main[0].branch_pattern == "main"
    error_message = "prod environment needs the reviewer and main-only deployments."
  }

  assert {
    condition     = !contains(keys(github_actions_variable.repo), "GCP_DEPLOY_SA")
    error_message = "Deploy identifiers must be environment-scoped, not repository-wide."
  }
}

run "bootstrap_with_org" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    enable_github = true
    org_id        = "000000000000"
  }

  assert {
    condition     = google_project.this.org_id == "000000000000" && length(google_organization_iam_member.deploy_vpcsc) == 1
    error_message = "With an Organization, the project is created under it and deploy can manage the scoped access policy."
  }
}

# --- GCP-only stage (enable_github defaults to false) ---

run "gcp_only_has_no_github_resources" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    github_owner_id = "1000"
    github_reviewer = null
  }

  assert {
    condition = alltrue([
      length(github_repository.this) == 0,
      length(github_repository_environment.prod) == 0,
      length(github_repository_environment_deployment_policy.prod_main) == 0,
      length(github_actions_environment_variable.prod) == 0,
      length(github_actions_variable.repo) == 0,
      length(data.github_user.owner) == 0,
      length(data.github_user.reviewer) == 0,
    ])
    error_message = "With enable_github = false no GitHub resource or data source may be planned (no token needed)."
  }

  assert {
    condition     = output.github_repository == null
    error_message = "github_repository output is null in the GCP-only stage."
  }

  assert {
    condition = alltrue([
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.repository == 'example-owner/example-repo'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.repository_owner_id == '1000'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.ref == 'refs/heads/main'"),
      strcontains(google_iam_workload_identity_pool_provider.deploy.attribute_condition, "assertion.environment == 'prod'"),
      strcontains(google_iam_workload_identity_pool_provider.review.attribute_condition, "assertion.repository_owner_id == '1000'"),
      strcontains(google_iam_workload_identity_pool_provider.review.attribute_condition, "assertion.job_workflow_ref == 'example-owner/example-repo/.github/workflows/security-review.yml@refs/heads/main'"),
    ])
    error_message = "WIF providers are created now, pinned to the future repo and the owner's numeric id."
  }

  assert {
    condition     = google_project_iam_member.review_vertex.role == "roles/aiplatform.user" && google_storage_bucket.state.public_access_prevention == "enforced"
    error_message = "GCP invariants hold in the GCP-only stage."
  }
}

run "gcp_only_requires_owner_id" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    github_owner_id = null
  }

  expect_failures = [
    google_iam_workload_identity_pool_provider.deploy,
    google_iam_workload_identity_pool_provider.review,
  ]
}

run "rejects_non_numeric_owner_id" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    github_owner_id = "example-owner"
  }

  expect_failures = [var.github_owner_id]
}

run "full_rejects_owner_id_mismatch" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    enable_github   = true
    github_owner_id = "2000"
  }

  expect_failures = [
    google_iam_workload_identity_pool_provider.deploy,
    google_iam_workload_identity_pool_provider.review,
  ]
}

run "full_requires_reviewer" {
  command = plan

  module {
    source = "../bootstrap"
  }

  variables {
    enable_github   = true
    github_reviewer = null
  }

  expect_failures = [data.github_user.reviewer]
}

run "finalize_rulesets" {
  command = plan

  module {
    source = "../github-finalize"
  }

  variables {
    github_owner = "example-owner"
    github_repo  = "example-repo"
  }

  assert {
    condition     = toset([for c in github_repository_ruleset.main_checks.rules[0].required_status_checks[0].required_check : c.context]) == toset(["ci", "security-review"])
    error_message = "main must require ci and security-review."
  }

  assert {
    condition     = length(github_repository_ruleset.main_checks.bypass_actors) == 0
    error_message = "Nobody bypasses the required checks."
  }

  assert {
    condition     = github_repository_ruleset.main_review.bypass_actors[0].bypass_mode == "pull_request"
    error_message = "The approval bypass applies to pull requests only."
  }
}
