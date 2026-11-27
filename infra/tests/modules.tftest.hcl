# Module-level invariants (each run plans one module as the root, so its
# resources can be asserted directly). Mock provider only.

mock_provider "google" {}

run "cloudsql_private_iam_pitr" {
  command = plan

  module {
    source = "../modules/cloudsql"
  }

  variables {
    project_id           = "proj-xxxx-0000"
    region               = "australia-southeast1"
    instance_name        = "jr-pg"
    database_name        = "app"
    network_id           = "projects/proj-xxxx-0000/global/networks/jr-vpc"
    psa_connection       = "fake-psa"
    iam_service_accounts = ["jr-api@proj-xxxx-0000.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_sql_database_instance.this.settings[0].ip_configuration[0].ipv4_enabled == false
    error_message = "Cloud SQL must have no public IP."
  }

  assert {
    condition     = google_sql_database_instance.this.settings[0].ip_configuration[0].private_network != null
    error_message = "Cloud SQL must use a private network."
  }

  assert {
    condition     = google_sql_database_instance.this.database_version == "POSTGRES_16" && google_sql_database_instance.this.settings[0].tier == "db-f1-micro"
    error_message = "Expected Postgres 16 on db-f1-micro."
  }

  assert {
    condition     = google_sql_database_instance.this.deletion_protection && google_sql_database_instance.this.settings[0].deletion_protection_enabled
    error_message = "Deletion protection must be on."
  }

  assert {
    condition = (
      google_sql_database_instance.this.settings[0].backup_configuration[0].enabled &&
      google_sql_database_instance.this.settings[0].backup_configuration[0].point_in_time_recovery_enabled
    )
    error_message = "Backups and PITR must be enabled."
  }

  assert {
    condition     = contains([for f in google_sql_database_instance.this.settings[0].database_flags : "${f.name}=${f.value}"], "cloudsql.iam_authentication=on")
    error_message = "IAM database auth must be on."
  }

  assert {
    condition     = google_sql_user.iam_sa["jr-api@proj-xxxx-0000.iam.gserviceaccount.com"].name == "jr-api@proj-xxxx-0000.iam" && google_sql_user.iam_sa["jr-api@proj-xxxx-0000.iam.gserviceaccount.com"].type == "CLOUD_IAM_SERVICE_ACCOUNT"
    error_message = "IAM DB users must be service-account users without the domain suffix."
  }
}

run "identity_platform_no_signup" {
  command = plan

  module {
    source = "../modules/identity_platform"
  }

  variables {
    project_id            = "proj-xxxx-0000"
    blocking_function_uri = "https://auth-blocking-xxxx.a.run.app"
  }

  assert {
    condition     = google_identity_platform_config.this.client[0].permissions[0].disabled_user_signup == true
    error_message = "Self-registration must be disabled."
  }

  assert {
    condition     = google_identity_platform_config.this.sign_in[0].anonymous[0].enabled == false
    error_message = "Anonymous sign-in must be disabled."
  }

  assert {
    condition     = google_identity_platform_config.this.sign_in[0].email[0].enabled && google_identity_platform_config.this.sign_in[0].email[0].password_required
    error_message = "Email/password sign-in must be enabled."
  }

  assert {
    condition     = length(google_identity_platform_config.this.mfa[0].provider_configs[0].totp_provider_config) == 1
    error_message = "TOTP MFA must be configured."
  }

  assert {
    condition     = toset([for t in google_identity_platform_config.this.blocking_functions[0].triggers : t.event_type]) == toset(["beforeCreate", "beforeSignIn"])
    error_message = "Both blocking functions must be wired."
  }
}

run "network_deny_all_egress" {
  command = plan

  module {
    source = "../modules/network"
  }

  variables {
    project_id         = "proj-xxxx-0000"
    region             = "australia-southeast1"
    name               = "jr-vpc"
    use_restricted_vip = true
  }

  assert {
    condition = (
      google_compute_firewall.sandbox_deny_all_egress.direction == "EGRESS" &&
      contains(google_compute_firewall.sandbox_deny_all_egress.destination_ranges, "0.0.0.0/0") &&
      one(google_compute_firewall.sandbox_deny_all_egress.deny).protocol == "all"
    )
    error_message = "Tagged jobs must have a deny-all egress rule."
  }

  assert {
    condition     = google_compute_firewall.sandbox_allow_google_apis.priority < google_compute_firewall.sandbox_deny_all_egress.priority
    error_message = "The VIP allow rule must take precedence over deny-all."
  }

  assert {
    condition     = google_compute_firewall.sandbox_allow_google_apis.destination_ranges == toset(["199.36.153.4/30"])
    error_message = "With VPC-SC, sandbox jobs may only reach restricted.googleapis.com."
  }

  assert {
    condition     = google_compute_subnetwork.run.private_ip_google_access
    error_message = "Private Google Access must be on."
  }

  assert {
    condition     = google_dns_record_set.googleapis_wildcard.rrdatas == tolist(["restricted.googleapis.com."])
    error_message = "*.googleapis.com must resolve to the restricted VIP."
  }
}

run "vpc_sc_perimeter" {
  command = plan

  module {
    source = "../modules/vpc_sc"
  }

  variables {
    org_id             = "000000000000"
    project_number     = "000000000000"
    name               = "jr"
    ingress_identities = ["serviceAccount:gh-deploy@proj-xxxx-0000.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_access_context_manager_access_policy.this.scopes == tolist(["projects/000000000000"])
    error_message = "The access policy must be scoped to the project."
  }

  assert {
    condition = alltrue([
      for s in ["aiplatform.googleapis.com", "storage.googleapis.com", "modelarmor.googleapis.com"] :
      contains(google_access_context_manager_service_perimeter.this.status[0].restricted_services, s)
    ])
    error_message = "Vertex, GCS and Model Armor must be restricted."
  }

  assert {
    condition     = google_access_context_manager_service_perimeter.this.status[0].vpc_accessible_services[0].enable_restriction
    error_message = "VPC-accessible services must be restricted."
  }
}

run "run_job_sandbox_shape" {
  command = plan

  module {
    source = "../modules/run_job"
  }

  variables {
    project_id      = "proj-xxxx-0000"
    region          = "australia-southeast1"
    name            = "jr-agent-writer"
    service_account = "jr-agent-writer@proj-xxxx-0000.iam.gserviceaccount.com"
    image           = "example.test/image"
    vpc = {
      network    = "net"
      subnetwork = "subnet"
      tags       = ["sandbox"]
    }
  }

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].execution_environment == "EXECUTION_ENVIRONMENT_GEN2"
    error_message = "Jobs must use the gen2 (microVM) execution environment."
  }

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].vpc_access[0].egress == "ALL_TRAFFIC"
    error_message = "VPC jobs must send all egress through the VPC."
  }

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].vpc_access[0].network_interfaces[0].tags == tolist(["sandbox"])
    error_message = "Sandbox tag must be applied."
  }

  assert {
    condition     = length([for e in google_cloud_run_v2_job.this.template[0].template[0].containers[0].env : e if length(e.value_source) > 0]) == 0
    error_message = "No secret env by default."
  }
}

run "run_job_push_no_vpc" {
  command = plan

  module {
    source = "../modules/run_job"
  }

  variables {
    project_id      = "proj-xxxx-0000"
    region          = "australia-southeast1"
    name            = "jr-push"
    service_account = "jr-push@proj-xxxx-0000.iam.gserviceaccount.com"
    image           = "example.test/image"
  }

  assert {
    condition     = length(google_cloud_run_v2_job.this.template[0].template[0].vpc_access) == 0
    error_message = "A job without vpc must use default egress."
  }
}

run "secrets_containers_only" {
  command = plan

  module {
    source = "../modules/secrets"
  }

  variables {
    project_id       = "proj-xxxx-0000"
    replica_location = "australia-southeast1"
    secrets = {
      "deploy-key" = ["serviceAccount:jr-push@proj-xxxx-0000.iam.gserviceaccount.com"]
    }
  }

  assert {
    condition     = google_secret_manager_secret_iam_member.accessor["deploy-key|serviceAccount:jr-push@proj-xxxx-0000.iam.gserviceaccount.com"].role == "roles/secretmanager.secretAccessor"
    error_message = "Accessors are granted per secret."
  }
}

run "scheduler_paused" {
  command = plan

  module {
    source = "../modules/scheduler"
  }

  variables {
    project_id      = "proj-xxxx-0000"
    region          = "australia-southeast1"
    service_account = "jr-scheduler@proj-xxxx-0000.iam.gserviceaccount.com"
    schedules = {
      redteam = { job = "jr-redteam-launcher", cron = "0 17 * * 3", paused = true }
    }
  }

  assert {
    condition     = google_cloud_scheduler_job.this["redteam"].paused
    error_message = "A disabled schedule must be paused."
  }
}
