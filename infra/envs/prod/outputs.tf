# Non-sensitive outputs only. Never output secret ids' values, API keys or the
# Identity Platform client config.

output "web_url" {
  description = "Public web URL (run.app)."
  value       = module.web.uri
}

output "api_url" {
  description = "API URL (IAM-protected)."
  value       = module.api.uri
}

output "job_names" {
  description = "Cloud Run job names."
  value       = sort([for j in module.job : j.name])
}

output "vpc_sc_enabled" {
  description = "Whether the VPC-SC perimeter is managed (Organization present)."
  value       = local.vpc_sc_enabled
}

output "sql_connection_name" {
  description = "Cloud SQL connection name."
  value       = module.cloudsql.connection_name
}

output "model_armor_template" {
  description = "Model Armor template name."
  value       = module.model_armor.template_name
}

output "verdict_signing_key" {
  description = "KMS key id used by the signer."
  value       = module.kms.key_ids["verdict-signing"]
}
