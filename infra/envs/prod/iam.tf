# One service account per service and job, least privilege.
#
# Emails are built from known values (not read from the resource) so plans,
# tests and infra/policy can check bindings before apply; every binding
# depends on the accounts existing.
#
# Sandbox identities (agent-*, test-runner, redteam): Vertex and their own GCS
# prefix only. No KMS, no Secret Manager, no GitHub.

locals {
  service_accounts = {
    "web"              = "Web frontend"
    "api"              = "API"
    "coordinator"      = "Sandbox coordinator (internal ingress)"
    "scheduler"        = "Cloud Scheduler caller"
    "auth-blocking"    = "Identity Platform blocking function"
    "feed-replay"      = "Job: feed replay"
    "digest"           = "Job: nightly digest"
    "rollup"           = "Job: weekly rollup"
    "watchdog"         = "Job: watchdog (leases, user count)"
    "evals"            = "Job: evals"
    "migrate"          = "Job: migrations"
    "seed"             = "Job: demo seed"
    "provision-users"  = "Job: provision the two accounts"
    "agent-lead"       = "Sandbox: agent lead"
    "agent-writer"     = "Sandbox: agent writer"
    "agent-reviewer"   = "Sandbox: agent reviewer"
    "test-runner"      = "Publisher: test runner (sandbox)"
    "signer"           = "Publisher: signer"
    "push"             = "Publisher: push"
    "redteam-launcher" = "Red team: launcher"
    "redteam"          = "Red team: agent (sandbox)"
    "tester-reset"     = "Red team: tester reset"
  }

  sa_email  = { for k, _ in local.service_accounts : k => "${var.name_prefix}-${k}@${var.project_id}.iam.gserviceaccount.com" }
  sa_member = { for k, e in local.sa_email : k => "serviceAccount:${e}" }

  sandbox_identities = ["agent-lead", "agent-writer", "agent-reviewer", "test-runner", "redteam"]

  observability_roles = ["roles/logging.logWriter", "roles/monitoring.metricWriter", "roles/cloudtrace.agent"]
  db_roles            = ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]
  ai_roles            = ["roles/aiplatform.user", "roles/modelarmor.user", "roles/dlp.user"]

  project_roles = {
    "web"              = []
    "api"              = concat(local.db_roles, local.ai_roles, ["roles/cloudtasks.enqueuer", "roles/errorreporting.writer"])
    "coordinator"      = local.db_roles
    "scheduler"        = []
    "auth-blocking"    = []
    "feed-replay"      = concat(local.db_roles, local.ai_roles)
    "digest"           = concat(local.db_roles, local.ai_roles)
    "rollup"           = concat(local.db_roles, local.ai_roles)
    "watchdog"         = concat(local.db_roles, ["roles/firebaseauth.viewer"])
    "evals"            = concat(local.db_roles, local.ai_roles)
    "migrate"          = local.db_roles
    "seed"             = concat(local.db_roles, ["roles/dlp.user"])
    "provision-users"  = ["roles/firebaseauth.admin"]
    "agent-lead"       = ["roles/aiplatform.user"]
    "agent-writer"     = ["roles/aiplatform.user"]
    "agent-reviewer"   = ["roles/aiplatform.user"]
    "test-runner"      = []
    "signer"           = []
    "push"             = []
    "redteam-launcher" = ["roles/firebaseauth.viewer"]
    "redteam"          = ["roles/aiplatform.user"]
    "tester-reset"     = local.db_roles
  }

  project_bindings = merge([
    for sa, roles in local.project_roles : {
      for role in distinct(concat(roles, local.observability_roles)) : "${sa}|${role}" => { sa = sa, role = role }
    }
  ]...)

  # Service accounts that get an IAM database user.
  db_users = [for sa, roles in local.project_roles : local.sa_email[sa] if contains(roles, "roles/cloudsql.instanceUser")]

  # Artifacts bucket, per-prefix grants (IAM conditions on the object name).
  artifact_grants = {
    "agent-lead|viewer|bundles/"                 = { sa = "agent-lead", role = "roles/storage.objectViewer", prefix = "bundles/" }
    "agent-lead|creator|results/agent-lead/"     = { sa = "agent-lead", role = "roles/storage.objectCreator", prefix = "results/agent-lead/" }
    "agent-writer|viewer|bundles/"               = { sa = "agent-writer", role = "roles/storage.objectViewer", prefix = "bundles/" }
    "agent-writer|creator|results/agent-writer/" = { sa = "agent-writer", role = "roles/storage.objectCreator", prefix = "results/agent-writer/" }
    "agent-reviewer|viewer|bundles/"             = { sa = "agent-reviewer", role = "roles/storage.objectViewer", prefix = "bundles/" }
    "agent-reviewer|viewer|results/agent-writer" = { sa = "agent-reviewer", role = "roles/storage.objectViewer", prefix = "results/agent-writer/" }
    "agent-reviewer|creator|results/reviewer/"   = { sa = "agent-reviewer", role = "roles/storage.objectCreator", prefix = "results/agent-reviewer/" }
    "test-runner|viewer|bundles/"                = { sa = "test-runner", role = "roles/storage.objectViewer", prefix = "bundles/" }
    "test-runner|viewer|results/agent-writer/"   = { sa = "test-runner", role = "roles/storage.objectViewer", prefix = "results/agent-writer/" }
    "test-runner|creator|results/test-runner/"   = { sa = "test-runner", role = "roles/storage.objectCreator", prefix = "results/test-runner/" }
    "signer|viewer|bundles/"                     = { sa = "signer", role = "roles/storage.objectViewer", prefix = "bundles/" }
    "signer|viewer|results/"                     = { sa = "signer", role = "roles/storage.objectViewer", prefix = "results/" }
    "signer|creator|verdicts/"                   = { sa = "signer", role = "roles/storage.objectCreator", prefix = "verdicts/" }
    "push|viewer|results/agent-writer/"          = { sa = "push", role = "roles/storage.objectViewer", prefix = "results/agent-writer/" }
    "push|viewer|verdicts/"                      = { sa = "push", role = "roles/storage.objectViewer", prefix = "verdicts/" }
    "redteam|creator|results/redteam/"           = { sa = "redteam", role = "roles/storage.objectCreator", prefix = "results/redteam/" }
  }
}

resource "google_service_account" "this" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key}"
  display_name = each.value
}

resource "google_project_iam_member" "this" {
  for_each = local.project_bindings

  project = var.project_id
  role    = each.value.role
  member  = local.sa_member[each.value.sa]

  depends_on = [google_service_account.this]
}

# The launcher signs the tester's custom token as itself (signBlob on its own
# account only). The red-team agent gets no token-creator permission.
resource "google_service_account_iam_member" "launcher_self_sign" {
  service_account_id = google_service_account.this["redteam-launcher"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.sa_member["redteam-launcher"]
}

# --- Buckets ---

resource "google_storage_bucket" "uploads" {
  project                     = var.project_id
  name                        = var.uploads_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = var.labels
}

resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = var.artifacts_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = var.artifact_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "uploads_api" {
  bucket = google_storage_bucket.uploads.name
  role   = "roles/storage.objectAdmin"
  member = local.sa_member["api"]

  depends_on = [google_service_account.this]
}

resource "google_storage_bucket_iam_member" "artifacts_api" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = local.sa_member["api"]

  depends_on = [google_service_account.this]
}

resource "google_storage_bucket_iam_member" "artifacts_coordinator" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = local.sa_member["coordinator"]

  depends_on = [google_service_account.this]
}

resource "google_storage_bucket_iam_member" "artifacts_prefix" {
  for_each = local.artifact_grants

  bucket = google_storage_bucket.artifacts.name
  role   = each.value.role
  member = local.sa_member[each.value.sa]

  condition {
    title      = "prefix-${substr(sha1(each.key), 0, 12)}"
    expression = "resource.name.startsWith(\"projects/_/buckets/${var.artifacts_bucket_name}/objects/${each.value.prefix}\")"
  }

  depends_on = [google_service_account.this]
}
