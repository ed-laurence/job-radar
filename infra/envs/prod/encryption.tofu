# OpenTofu-only (Terraform ignores *.tofu files). Client-side state and plan
# encryption with the KMS key created by infra/bootstrap. Prod state is
# created encrypted from the first apply, so there is no unencrypted fallback
# and unencrypted state is refused.

terraform {
  encryption {
    key_provider "gcp_kms" "state" {
      kms_encryption_key = var.state_encryption_key
      key_length         = 32
    }

    method "aes_gcm" "state" {
      keys = key_provider.gcp_kms.state
    }

    state {
      method   = method.aes_gcm.state
      enforced = true
    }

    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}
