output "key_ring_id" {
  description = "Key ring id."
  value       = google_kms_key_ring.this.id
}

output "key_ids" {
  description = "Key name => crypto key id."
  value       = { for k, v in google_kms_crypto_key.this : k => v.id }
}
