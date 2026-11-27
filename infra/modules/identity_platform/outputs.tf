output "config_name" {
  description = "Identity Platform config resource name."
  value       = google_identity_platform_config.this.name
}

output "blocking_functions_enabled" {
  description = "Whether the blocking functions are wired up."
  value       = var.blocking_function_uri != null
}
