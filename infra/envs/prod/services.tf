# Cloud Run services. Only `web` is public (allUsers); `api` requires IAM
# (web, the red team and the uptime checker); `coordinator` is internal-only
# for sandbox agent traffic.

locals {
  app_vpc = {
    network    = module.network.network_id
    subnetwork = module.network.subnet_id
    egress     = "ALL_TRAFFIC"
    tags       = []
  }

  app_env = {
    APP_ENV              = "prod"
    DB_CONNECTION_NAME   = module.cloudsql.connection_name
    DB_NAME              = module.cloudsql.database_name
    MODEL_ARMOR_TEMPLATE = module.model_armor.template_name
    SDP_INSPECT_TEMPLATE = module.sdp.inspect_template
    SDP_REDACT_TEMPLATE  = module.sdp.redact_template
  }

  # VERIFY (M0 spike): the identity Cloud Monitoring uses for uptime checks
  # with service-agent OIDC authentication.
  uptime_checker = "serviceAccount:service-${var.project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"

  # roles/run.invoker per service. Only web is public.
  service_invokers = {
    web         = ["allUsers"]
    api         = [local.sa_member["web"], local.sa_member["redteam"], local.uptime_checker]
    coordinator = [for sa in ["agent-lead", "agent-writer", "agent-reviewer"] : local.sa_member[sa]]
  }
}

module "web" {
  source = "../../modules/run_service"

  project_id      = var.project_id
  region          = var.region
  name            = "${var.name_prefix}-web"
  service_account = local.sa_email["web"]
  image           = var.placeholder_image
  ingress         = "INGRESS_TRAFFIC_ALL"
  invoker_members = local.service_invokers.web
  max_instances   = var.max_instances.web
  labels          = var.labels

  env = {
    APP_ENV = "prod"
    API_URL = module.api.uri
  }

  depends_on = [google_service_account.this]
}

module "api" {
  source = "../../modules/run_service"

  project_id      = var.project_id
  region          = var.region
  name            = "${var.name_prefix}-api"
  service_account = local.sa_email["api"]
  image           = var.placeholder_image
  ingress         = "INGRESS_TRAFFIC_ALL"
  vpc             = local.app_vpc
  max_instances   = var.max_instances.api
  memory          = "1Gi"
  timeout         = "900s"
  labels          = var.labels

  invoker_members = local.service_invokers.api

  env = merge(local.app_env, {
    ARTIFACTS_BUCKET = google_storage_bucket.artifacts.name
    UPLOADS_BUCKET   = google_storage_bucket.uploads.name
    STORAGE          = "gcs"
    STORAGE_BUCKET   = google_storage_bucket.uploads.name
    TASKS_QUEUE      = google_cloud_tasks_queue.agent_dispatch.id
    DB_POOL_SIZE     = "5"
  })

  secret_env = {
    APP_USERS             = { secret = module.secrets.secret_ids[var.secret_ids.app_users] }
    DENYLIST              = { secret = module.secrets.secret_ids[var.secret_ids.denylist] }
    WEBHOOK_HMAC          = { secret = module.secrets.secret_ids[var.secret_ids.webhook_hmac] }
    COORDINATOR_TOKEN_KEY = { secret = module.secrets.secret_ids[var.secret_ids.coordinator_key] }
  }

  depends_on = [google_service_account.this, module.secrets]
}

module "coordinator" {
  source = "../../modules/run_service"

  project_id      = var.project_id
  region          = var.region
  name            = "${var.name_prefix}-coordinator"
  service_account = local.sa_email["coordinator"]
  image           = var.placeholder_image
  ingress         = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  vpc             = local.app_vpc
  max_instances   = var.max_instances.coordinator
  labels          = var.labels

  invoker_members = local.service_invokers.coordinator

  env = merge(local.app_env, { DB_POOL_SIZE = "2" })

  secret_env = {
    DENYLIST              = { secret = module.secrets.secret_ids[var.secret_ids.denylist] }
    COORDINATOR_TOKEN_KEY = { secret = module.secrets.secret_ids[var.secret_ids.coordinator_key] }
  }

  depends_on = [google_service_account.this, module.secrets]
}
