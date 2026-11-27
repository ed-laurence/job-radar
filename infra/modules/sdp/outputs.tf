output "inspect_template" {
  description = "Inspect template name."
  value       = google_data_loss_prevention_inspect_template.this.name
}

output "redact_template" {
  description = "Redact de-identify template name."
  value       = google_data_loss_prevention_deidentify_template.redact.name
}

output "tokenize_template" {
  description = "Tokenise de-identify template name, or null."
  value       = local.tokenize ? google_data_loss_prevention_deidentify_template.tokenize[0].name : null
}
