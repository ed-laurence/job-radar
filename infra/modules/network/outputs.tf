output "network_id" {
  description = "VPC id."
  value       = google_compute_network.this.id
}

output "network_name" {
  description = "VPC name."
  value       = google_compute_network.this.name
}

output "subnet_id" {
  description = "Direct VPC egress subnet id."
  value       = google_compute_subnetwork.run.id
}

output "psa_connection" {
  description = "Private Service Access connection id (Cloud SQL depends on it)."
  value       = google_service_networking_connection.psa.id
}

output "sandbox_tags" {
  description = "Tags that receive the deny-all egress firewall."
  value       = var.sandbox_tags
}

output "google_apis_vip_cidr" {
  description = "VIP range that deny-all jobs may reach."
  value       = local.vip_cidr
}
