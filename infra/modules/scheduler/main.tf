# Cloud Scheduler -> Cloud Run Admin API jobs:run, authenticated with an OAuth
# token for a dedicated scheduler service account that can only run the
# scheduled jobs.

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  for_each = toset(distinct([for s in var.schedules : s.job]))

  project  = var.project_id
  location = var.region
  name     = each.value
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account}"
}

resource "google_cloud_scheduler_job" "this" {
  for_each = var.schedules

  project          = var.project_id
  region           = var.region
  name             = each.key
  schedule         = each.value.cron
  time_zone        = var.time_zone
  paused           = each.value.paused
  attempt_deadline = "180s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${each.value.job}:run"

    oauth_token {
      service_account_email = var.service_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
