# Cloud SQL Postgres 16: private IP only, IAM database auth, backups + PITR.
# No passwords are created here, so nothing secret lands in state.

resource "google_sql_database_instance" "this" {
  project             = var.project_id
  name                = var.instance_name
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.tier
    edition                     = "ENTERPRISE"
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    user_labels                 = var.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.network_id
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = var.backup_start_time
      transaction_log_retention_days = var.transaction_log_retention_days

      backup_retention_settings {
        retained_backups = var.retained_backups
        retention_unit   = "COUNT"
      }
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    database_flags {
      name  = "log_min_duration_statement"
      value = "500"
    }
  }

  depends_on = [terraform_data.psa_dependency]
}

# Makes the instance wait for the Private Service Access peering.
resource "terraform_data" "psa_dependency" {
  input = var.psa_connection
}

resource "google_sql_database" "app" {
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = var.database_name
}

# IAM database users. The Postgres user name is the SA email without
# ".gserviceaccount.com". Postgres grants are applied by the migrate job.
resource "google_sql_user" "iam_sa" {
  for_each = toset(var.iam_service_accounts)

  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
