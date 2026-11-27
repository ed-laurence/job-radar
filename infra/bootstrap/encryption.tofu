# OpenTofu-only (Terraform ignores *.tofu files): client-side state and plan
# encryption with the GCP KMS key provider (OpenTofu >= 1.8 for variables in
# the encryption block).
#
# Two-pass flow (scripts/bootstrap.sh):
#   1. The KMS key doesn't exist yet, so pass 1 moves this file aside
#      (encryption.tofu -> encryption.tofu.off), applies with LOCAL state on
#      the owner's machine (state holds no secrets by design), then restores it.
#   2. Pass 2 writes the untracked backend_override.tf (gcs backend, the state
#      bucket, prefix "bootstrap") and runs `tofu init -migrate-state`. The
#      fallback below reads the old unencrypted state; every write is encrypted.
#   3. After the migration, delete the two fallback blocks and set
#      enforced = true, so unencrypted state is refused from then on.

locals {
  state_encryption_key = "projects/${var.project_id}/locations/${var.region}/keyRings/${var.state_key_ring_name}/cryptoKeys/${var.state_key_name}"
}

terraform {
  encryption {
    key_provider "gcp_kms" "state" {
      kms_encryption_key = local.state_encryption_key
      key_length         = 32
    }

    method "aes_gcm" "state" {
      keys = key_provider.gcp_kms.state
    }

    method "unencrypted" "migrate" {}

    state {
      method = method.aes_gcm.state

      fallback {
        method = method.unencrypted.migrate
      }
    }

    plan {
      method = method.aes_gcm.state

      fallback {
        method = method.unencrypted.migrate
      }
    }
  }
}
