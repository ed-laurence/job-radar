# Secret Manager secret CONTAINERS only. Values are written outside OpenTofu
# (bootstrap.sh via gcloud), so no secret material ever lands in state.
# Access is granted per secret, never at project level.

locals {
  bindings = merge([
    for secret, members in var.secrets : {
      for m in members : "${secret}|${m}" => { secret = secret, member = m }
    }
  ]...)
}

resource "google_secret_manager_secret" "this" {
  for_each = var.secrets

  project   = var.project_id
  secret_id = each.key
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.replica_location
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each = local.bindings

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
