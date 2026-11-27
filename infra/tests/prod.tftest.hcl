# Prod composition invariants, planned against a mock provider (no
# credentials, no API calls). Run from infra/tests:
#   terraform init && terraform test      (or: tofu init && tofu test)

mock_provider "google" {}

variables {
  project_id             = "proj-xxxx-0000"
  project_number         = "000000000000"
  name_prefix            = "jr"
  state_encryption_key   = "projects/proj-xxxx-0000/locations/australia-southeast1/keyRings/tofu-state/cryptoKeys/state"
  deploy_service_account = "gh-deploy@proj-xxxx-0000.iam.gserviceaccount.com"
  review_service_account = "gh-security-review@proj-xxxx-0000.iam.gserviceaccount.com"
  notification_channels  = ["projects/proj-xxxx-0000/notificationChannels/0"]
  uploads_bucket_name    = "proj-xxxx-0000-uploads"
  artifacts_bucket_name  = "proj-xxxx-0000-artifacts"
}

run "prod_without_org" {
  command = plan

  module {
    source = "../envs/prod"
  }

  assert {
    condition     = var.region == "australia-southeast1"
    error_message = "Region must default to australia-southeast1."
  }

  assert {
    condition     = length(module.vpc_sc) == 0 && !local.vpc_sc_enabled
    error_message = "VPC-SC must not be created without an Organization."
  }

  assert {
    condition     = module.network.google_apis_vip_cidr == "199.36.153.8/30"
    error_message = "Without an Organization, deny-all jobs must use the private.googleapis.com VIP."
  }

  assert {
    condition     = alltrue([for t in ["sandbox", "publisher", "redteam"] : contains(module.network.sandbox_tags, t)])
    error_message = "Sandbox, publisher and red-team tags must all receive the deny-all egress firewall."
  }

  # --- sandbox identities ---

  assert {
    condition = alltrue([
      for sa in local.sandbox_identities : !contains(flatten(values(local.secret_accessors)), local.sa_member[sa])
    ])
    error_message = "Sandbox identities must not access any secret."
  }

  assert {
    condition = alltrue([
      for sa in local.sandbox_identities : !contains(flatten(values(local.verdict_key_iam)), local.sa_member[sa])
    ])
    error_message = "Sandbox identities (incl. test-runner) must have no KMS access."
  }

  assert {
    condition = alltrue([
      for b in google_project_iam_member.this :
      contains(concat(["roles/aiplatform.user"], local.observability_roles), b.role)
      if contains([for sa in local.sandbox_identities : local.sa_member[sa]], b.member)
    ])
    error_message = "Sandbox identities may only hold Vertex and observability roles at project level."
  }

  assert {
    condition = alltrue([
      for b in google_project_iam_member.this : contains(local.observability_roles, b.role)
      if b.member == local.sa_member["test-runner"]
    ])
    error_message = "test-runner must hold no project role beyond logging/metrics/trace."
  }

  assert {
    condition     = alltrue([for sa in local.sandbox_identities : length(try(local.jobs[sa].secrets, {})) == 0])
    error_message = "Sandbox jobs must not mount secrets."
  }

  assert {
    condition = alltrue([
      for sa in ["agent-lead", "agent-writer", "agent-reviewer", "test-runner"] : local.jobs[sa].mode == "sandbox"
    ]) && local.jobs["redteam"].mode == "redteam" && local.jobs["signer"].mode == "publisher"
    error_message = "Agent, test-runner, signer and red-team jobs must run behind the deny-all firewall."
  }

  # --- publisher split ---

  assert {
    condition     = local.secret_accessors["deploy-key"] == [local.sa_member["push"]]
    error_message = "Only push may read the deploy key."
  }

  assert {
    condition     = local.jobs["push"].mode == "none"
    error_message = "push runs outside the VPC (default egress to reach github.com)."
  }

  assert {
    condition     = local.verdict_key_iam["roles/cloudkms.signer"] == [local.sa_member["signer"]]
    error_message = "Only the signer may sign verdicts."
  }

  # --- public exposure ---

  assert {
    condition = alltrue([
      for svc, members in local.service_invokers :
      svc == "web" || (!contains(members, "allUsers") && !contains(members, "allAuthenticatedUsers"))
    ])
    error_message = "Only the web service may be public."
  }

  assert {
    condition     = google_storage_bucket.artifacts.public_access_prevention == "enforced" && google_storage_bucket.uploads.public_access_prevention == "enforced"
    error_message = "Buckets must enforce public access prevention."
  }

  assert {
    condition     = google_storage_bucket.artifacts.uniform_bucket_level_access && google_storage_bucket.uploads.uniform_bucket_level_access
    error_message = "Buckets must use uniform bucket-level access (needed for prefix IAM conditions)."
  }

  assert {
    condition     = length(module.job) == 17
    error_message = "Expected the 17 jobs from the Architecture list."
  }
}

run "prod_with_org" {
  command = plan

  module {
    source = "../envs/prod"
  }

  variables {
    org_id = "000000000000"
  }

  assert {
    condition     = length(module.vpc_sc) == 1
    error_message = "VPC-SC must be created when an Organization exists."
  }

  assert {
    condition     = module.network.google_apis_vip_cidr == "199.36.153.4/30"
    error_message = "With an Organization, Google APIs must resolve to restricted.googleapis.com."
  }
}

run "rejects_bad_prefix" {
  command = plan

  module {
    source = "../envs/prod"
  }

  variables {
    name_prefix = "Way-Too-Long-Prefix"
  }

  expect_failures = [var.name_prefix]
}
