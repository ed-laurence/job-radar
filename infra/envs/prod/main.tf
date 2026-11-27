# Prod composition. See infra/README.md for how this relates to bootstrap.

locals {
  vpc_sc_enabled       = var.org_id != null
  model_armor_location = coalesce(var.model_armor_location, var.region)

  # Tags that receive the deny-all egress firewall.
  sandbox_tag   = "sandbox"
  publisher_tag = "publisher"
  redteam_tag   = "redteam"

  # Per-secret accessors. No sandbox identity may appear here.
  secret_accessors = {
    (var.secret_ids.app_users)       = [for sa in ["api", "auth-blocking", "provision-users", "watchdog", "redteam-launcher"] : local.sa_member[sa]]
    (var.secret_ids.denylist)        = [for sa in ["api", "coordinator", "signer"] : local.sa_member[sa]]
    (var.secret_ids.deploy_key)      = [local.sa_member["push"]]
    (var.secret_ids.webhook_hmac)    = [local.sa_member["api"]]
    (var.secret_ids.coordinator_key) = [for sa in ["api", "coordinator"] : local.sa_member[sa]]
  }

  # Verdict signing key: only the signer signs; push reads the public key.
  verdict_key_iam = {
    "roles/cloudkms.signer"          = [local.sa_member["signer"]]
    "roles/cloudkms.publicKeyViewer" = [local.sa_member["push"]]
  }
}

module "network" {
  source = "../../modules/network"

  project_id         = var.project_id
  region             = var.region
  name               = "${var.name_prefix}-vpc"
  sandbox_tags       = [local.sandbox_tag, local.publisher_tag, local.redteam_tag]
  use_restricted_vip = local.vpc_sc_enabled
  labels             = var.labels
}

module "vpc_sc" {
  source = "../../modules/vpc_sc"
  count  = local.vpc_sc_enabled ? 1 : 0

  org_id         = var.org_id
  project_number = var.project_number
  name           = var.name_prefix
  dry_run        = var.vpc_sc_dry_run
  ingress_identities = concat(
    ["serviceAccount:${var.deploy_service_account}", "serviceAccount:${var.review_service_account}"],
    var.perimeter_admin_members,
  )
}

module "cloudsql" {
  source = "../../modules/cloudsql"

  project_id           = var.project_id
  region               = var.region
  instance_name        = "${var.name_prefix}-pg"
  database_name        = var.database_name
  network_id           = module.network.network_id
  psa_connection       = module.network.psa_connection
  iam_service_accounts = local.db_users
  labels               = var.labels

  depends_on = [google_service_account.this]
}

module "kms" {
  source = "../../modules/kms"

  project_id    = var.project_id
  location      = var.region
  key_ring_name = "${var.name_prefix}-app"
  labels        = var.labels

  keys = {
    "verdict-signing" = {
      purpose   = "ASYMMETRIC_SIGN"
      algorithm = "EC_SIGN_P256_SHA256"
      iam       = local.verdict_key_iam
    }
  }

  depends_on = [google_service_account.this]
}

# Secret containers and per-secret accessors. No sandbox identity appears here
# (enforced by infra/policy/sandbox_no_secrets.rego).
module "secrets" {
  source = "../../modules/secrets"

  project_id       = var.project_id
  replica_location = var.region
  labels           = var.labels

  secrets = local.secret_accessors

  depends_on = [google_service_account.this]
}

module "identity_platform" {
  source = "../../modules/identity_platform"

  project_id            = var.project_id
  blocking_function_uri = var.blocking_function_uri
  authorized_domains = concat(
    [trimsuffix(trimprefix(module.web.uri, "https://"), "/")],
    var.extra_authorized_domains,
  )
}

module "sdp" {
  source = "../../modules/sdp"

  project_id           = var.project_id
  location             = var.region
  name                 = var.name_prefix
  tokenize_kms_key     = var.sdp_tokenize_kms_key
  tokenize_wrapped_key = var.sdp_tokenize_wrapped_key
}

module "model_armor" {
  source = "../../modules/model_armor"

  project_id  = var.project_id
  location    = local.model_armor_location
  template_id = "${var.name_prefix}-guard"
}

resource "google_cloud_tasks_queue" "agent_dispatch" {
  project  = var.project_id
  location = var.region
  name     = "${var.name_prefix}-agent-dispatch"

  rate_limits {
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 2
  }

  retry_config {
    max_attempts = 3
  }
}

module "scheduler" {
  source = "../../modules/scheduler"

  project_id      = var.project_id
  region          = var.region
  service_account = local.sa_email["scheduler"]
  time_zone       = var.time_zone

  schedules = {
    "${var.name_prefix}-feed-replay" = { job = module.job["feed-replay"].name, cron = var.schedules.feed_replay }
    "${var.name_prefix}-digest"      = { job = module.job["digest"].name, cron = var.schedules.digest }
    "${var.name_prefix}-rollup"      = { job = module.job["rollup"].name, cron = var.schedules.rollup }
    "${var.name_prefix}-watchdog"    = { job = module.job["watchdog"].name, cron = var.schedules.watchdog }
    "${var.name_prefix}-evals"       = { job = module.job["evals"].name, cron = var.schedules.evals }
    "${var.name_prefix}-redteam" = {
      job    = module.job["redteam-launcher"].name
      cron   = var.schedules.redteam
      paused = !var.redteam_enabled
    }
  }
}

module "monitoring" {
  source = "../../modules/monitoring"

  project_id            = var.project_id
  name                  = var.name_prefix
  notification_channels = var.notification_channels

  uptime_checks = {
    web = { uri = module.web.uri, path = "/" }
    api = { uri = module.api.uri, path = "/healthz", oidc = true }
  }

  availability_slos = {
    web = module.web.name
    api = module.api.name
  }
}
