output "access_policy_name" {
  description = "Access policy resource name (numeric id)."
  value       = google_access_context_manager_access_policy.this.name
}

output "perimeter_name" {
  description = "Service perimeter resource name."
  value       = google_access_context_manager_service_perimeter.this.name
}
