# Cloud Run jobs (gen2 microVM), one service account each.
#
# Network modes:
#   app       Direct VPC egress, no tag: Cloud SQL private IP + Google APIs via the VIP.
#   sandbox   Direct VPC egress + "sandbox" tag: deny-all egress except the VIP.
#   publisher Direct VPC egress + "publisher" tag (signer): same deny-all.
#   redteam   Direct VPC egress + "redteam" tag: same deny-all; reaches run.app via the VIP.
#   none      Default Cloud Run egress (push only: it must reach github.com).
#
# Sandbox jobs get no identifiers or secrets in their environment; the API
# passes per-execution paths as overrides (roles/run.jobsExecutorWithOverrides).

locals {
  jobs = {
    "feed-replay"     = { mode = "app" }
    "digest"          = { mode = "app" }
    "rollup"          = { mode = "app" }
    "watchdog"        = { mode = "app", secrets = { APP_USERS = var.secret_ids.app_users } }
    "evals"           = { mode = "app", cpu = "2", memory = "2Gi", timeout = "3600s" }
    "migrate"         = { mode = "app" }
    "seed"            = { mode = "app" }
    "provision-users" = { mode = "app", secrets = { APP_USERS = var.secret_ids.app_users } }
    "tester-reset"    = { mode = "app" }

    "agent-lead"     = { mode = "sandbox", cpu = "2", memory = "4Gi", timeout = "3600s", executors = ["api"] }
    "agent-writer"   = { mode = "sandbox", cpu = "2", memory = "4Gi", timeout = "3600s", executors = ["api"] }
    "agent-reviewer" = { mode = "sandbox", cpu = "2", memory = "4Gi", timeout = "3600s", executors = ["api"] }

    "test-runner" = { mode = "sandbox", cpu = "2", memory = "4Gi", timeout = "1800s", executors = ["api"] }
    "signer"      = { mode = "publisher", executors = ["api"], secrets = { DENYLIST = var.secret_ids.denylist } }
    "push"        = { mode = "none", executors = ["api"], secrets = { DEPLOY_KEY = var.secret_ids.deploy_key } }

    "redteam-launcher" = { mode = "app", executors = ["api"], secrets = { APP_USERS = var.secret_ids.app_users } }
    "redteam"          = { mode = "redteam", cpu = "2", memory = "2Gi", timeout = "3600s", executors = ["redteam-launcher"] }
  }

  job_tags = {
    app       = []
    sandbox   = [local.sandbox_tag]
    publisher = [local.publisher_tag]
    redteam   = [local.redteam_tag]
  }

  # tester-reset is started by the launcher before and after each run.
  extra_executors = {
    "tester-reset" = ["redteam-launcher"]
  }
}

module "job" {
  source   = "../../modules/run_job"
  for_each = local.jobs

  project_id      = var.project_id
  region          = var.region
  name            = "${var.name_prefix}-${each.key}"
  service_account = local.sa_email[each.key]
  image           = var.placeholder_image
  cpu             = try(each.value.cpu, "1")
  memory          = try(each.value.memory, "512Mi")
  timeout         = try(each.value.timeout, "600s")
  labels          = merge(var.labels, { network-mode = each.value.mode })

  vpc = each.value.mode == "none" ? null : {
    network    = module.network.network_id
    subnetwork = module.network.subnet_id
    egress     = "ALL_TRAFFIC"
    tags       = local.job_tags[each.value.mode]
  }

  env = merge(
    { JOB_NAME = each.key },
    each.value.mode == "app" ? merge(local.app_env, { DB_POOL_SIZE = "2" }) : {},
  )

  secret_env = {
    for name, id in try(each.value.secrets, {}) : name => { secret = module.secrets.secret_ids[id] }
  }

  executor_members = [
    for sa in concat(try(each.value.executors, []), lookup(local.extra_executors, each.key, [])) : local.sa_member[sa]
  ]

  depends_on = [google_service_account.this, module.secrets]
}
