output "name" {
  description = "Service name."
  value       = google_cloud_run_v2_service.this.name
}

output "id" {
  description = "Service id."
  value       = google_cloud_run_v2_service.this.id
}

output "uri" {
  description = "run.app URL."
  value       = google_cloud_run_v2_service.this.uri
}
