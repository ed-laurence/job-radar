output "secret_ids" {
  description = "Secret id => full resource id."
  value       = { for k, s in google_secret_manager_secret.this : k => s.id }
}
