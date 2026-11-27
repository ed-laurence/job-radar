# Project, billing, APIs, state bucket + KMS key, Artifact Registry, budget.

locals {
  # Built from known values so for_each keys are known at plan time.
  deploy_member = "serviceAccount:gh-deploy@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_project" "this" {
  project_id          = var.project_id
  name                = var.project_name
  org_id              = var.folder_id == null ? var.org_id : null
  folder_id           = var.folder_id
  billing_account     = var.billing_account
  auto_create_network = false
  deletion_policy     = "PREVENT"
  labels              = var.labels
}

resource "google_project_service" "this" {
  for_each = toset(var.apis)

  project            = google_project.this.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- OpenTofu state ---

module "state_kms" {
  source = "../modules/kms"

  project_id    = google_project.this.project_id
  location      = var.region
  key_ring_name = var.state_key_ring_name
  labels        = var.labels

  keys = {
    (var.state_key_name) = {
      purpose         = "ENCRYPT_DECRYPT"
      algorithm       = "GOOGLE_SYMMETRIC_ENCRYPTION"
      rotation_period = "7776000s"
      iam = {
        "roles/cloudkms.cryptoKeyEncrypterDecrypter" = concat(
          [local.deploy_member],
          var.state_key_users,
        )
      }
    }
  }

  depends_on = [google_project_service.this, google_service_account.deploy]
}

resource "google_storage_bucket" "state" {
  project                     = google_project.this.project_id
  name                        = var.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.this]
}

resource "google_storage_bucket_iam_member" "state_deploy" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deploy.email}"
}

# --- Artifact Registry (container images only) ---

resource "google_artifact_registry_repository" "images" {
  project       = google_project.this.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  format        = "DOCKER"
  description   = "Digest-pinned application and sandbox images."
  labels        = var.labels

  docker_config {
    immutable_tags = true
  }

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s"
    }
  }

  depends_on = [google_project_service.this]
}

# --- Alerts and budget ---

resource "google_monitoring_notification_channel" "email" {
  project      = google_project.this.project_id
  display_name = "Owner email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.this]
}

resource "google_billing_budget" "this" {
  provider = google.billing

  billing_account = var.billing_account
  display_name    = "${var.project_id} monthly"

  budget_filter {
    projects               = ["projects/${google_project.this.number}"]
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.budget_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.this]
}
