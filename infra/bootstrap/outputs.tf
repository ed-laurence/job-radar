# Non-sensitive outputs only. bootstrap.sh copies them into the untracked
# ~/.config/jobradar/prod.tfvars; they are never committed.

output "project_id" {
  description = "Project id."
  value       = google_project.this.project_id
}

output "project_number" {
  description = "Project number."
  value       = google_project.this.number
}

output "state_bucket" {
  description = "State bucket name."
  value       = google_storage_bucket.state.name
}

output "state_kms_key" {
  description = "KMS key id for OpenTofu state encryption."
  value       = module.state_kms.key_ids[var.state_key_name]
}

output "deploy_service_account" {
  description = "Deploy service account email."
  value       = google_service_account.deploy.email
}

output "deploy_wif_provider" {
  description = "Deploy WIF provider resource name."
  value       = google_iam_workload_identity_pool_provider.deploy.name
}

output "review_service_account" {
  description = "Review service account email (Vertex only)."
  value       = google_service_account.review.email
}

output "review_wif_provider" {
  description = "Review WIF provider resource name."
  value       = google_iam_workload_identity_pool_provider.review.name
}

output "artifact_registry" {
  description = "Docker repository host path."
  value       = "${var.region}-docker.pkg.dev/${google_project.this.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "notification_channel" {
  description = "Alert notification channel id."
  value       = google_monitoring_notification_channel.email.id
}

output "github_repository" {
  description = "Repository full name. Null in the GCP-only stage (enable_github = false)."
  value       = one(github_repository.this[*].full_name)
}

output "github_enabled" {
  description = "Whether this state manages the GitHub resources (bootstrap.sh refuses to go back to GCP-only)."
  value       = var.enable_github
}
