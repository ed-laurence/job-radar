# A Cloud Run job on the gen2 execution environment (microVM).
# Cloud Run has no read-only root filesystem flag: the image runs as non-root,
# and only the in-memory scratch volumes are meant to be written.

resource "google_cloud_run_v2_job" "this" {
  project             = var.project_id
  location            = var.region
  name                = var.name
  deletion_protection = var.deletion_protection
  labels              = var.labels

  template {
    task_count = var.task_count

    template {
      service_account       = var.service_account
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      timeout               = var.timeout
      max_retries           = var.max_retries

      dynamic "vpc_access" {
        for_each = var.vpc == null ? [] : [var.vpc]
        content {
          egress = vpc_access.value.egress
          network_interfaces {
            network    = vpc_access.value.network
            subnetwork = vpc_access.value.subnetwork
            tags       = vpc_access.value.tags
          }
        }
      }

      dynamic "volumes" {
        for_each = var.scratch_volumes
        content {
          name = volumes.key
          empty_dir {
            medium     = "MEMORY"
            size_limit = var.scratch_size_limit
          }
        }
      }

      containers {
        image = var.image
        args  = var.args

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "volume_mounts" {
          for_each = var.scratch_volumes
          content {
            name       = volume_mounts.key
            mount_path = volume_mounts.value
          }
        }

        dynamic "env" {
          for_each = var.env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret
                version = env.value.version
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
    ]
  }
}

resource "google_cloud_run_v2_job_iam_member" "executor" {
  for_each = toset(var.executor_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = each.value
}

resource "google_cloud_run_v2_job_iam_member" "invoker" {
  for_each = toset(var.invoker_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
