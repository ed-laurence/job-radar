# KMS key ring and keys (verdict signing, OpenTofu state encryption, SDP
# tokenisation). Keys cannot be deleted in GCP; prevent_destroy makes an
# accidental plan fail loudly instead of orphaning versions.

locals {
  key_bindings = merge([
    for key, cfg in var.keys : merge([
      for role, members in cfg.iam : {
        for m in members : "${key}|${role}|${m}" => { key = key, role = role, member = m }
      }
    ]...)
  ]...)
}

resource "google_kms_key_ring" "this" {
  project  = var.project_id
  name     = var.key_ring_name
  location = var.location
}

resource "google_kms_crypto_key" "this" {
  for_each = var.keys

  name            = each.key
  key_ring        = google_kms_key_ring.this.id
  purpose         = each.value.purpose
  rotation_period = each.value.purpose == "ENCRYPT_DECRYPT" ? each.value.rotation_period : null
  labels          = var.labels

  version_template {
    algorithm        = each.value.algorithm
    protection_level = each.value.protection_level
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "this" {
  for_each = local.key_bindings

  crypto_key_id = google_kms_crypto_key.this[each.value.key].id
  role          = each.value.role
  member        = each.value.member
}
