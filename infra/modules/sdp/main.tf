# Sensitive Data Protection templates:
# - inspect:  uploads, the seed gate and log/prompt redaction.
# - redact:   replace findings with their infoType (logs, llm_calls, shared views).
# - tokenize: deterministic tokenisation before prompts (only when a KMS-wrapped
#             key is supplied; the wrapped key is ciphertext, never a raw key).

locals {
  parent = "projects/${var.project_id}/locations/${var.location}"
  # count must not depend on the sensitive wrapped key, so only the KMS key gates it.
  tokenize = var.tokenize_kms_key != null
}

resource "google_data_loss_prevention_inspect_template" "this" {
  parent       = local.parent
  template_id  = "${var.name}-inspect"
  display_name = "${var.name} inspect"
  description  = "PII and credential inspection."

  inspect_config {
    min_likelihood = var.min_likelihood
    include_quote  = false

    dynamic "info_types" {
      for_each = var.info_types
      content {
        name = info_types.value
      }
    }
  }
}

resource "google_data_loss_prevention_deidentify_template" "redact" {
  parent       = local.parent
  template_id  = "${var.name}-redact"
  display_name = "${var.name} redact"
  description  = "Replace findings with their infoType."

  deidentify_config {
    info_type_transformations {
      transformations {
        dynamic "info_types" {
          for_each = var.info_types
          content {
            name = info_types.value
          }
        }

        primitive_transformation {
          replace_with_info_type_config = true
        }
      }
    }
  }
}

resource "google_data_loss_prevention_deidentify_template" "tokenize" {
  count = local.tokenize ? 1 : 0

  parent       = local.parent
  template_id  = "${var.name}-tokenize"
  display_name = "${var.name} tokenize"
  description  = "Deterministic tokenisation of PII before prompts."

  deidentify_config {
    info_type_transformations {
      transformations {
        dynamic "info_types" {
          for_each = var.info_types
          content {
            name = info_types.value
          }
        }

        primitive_transformation {
          crypto_deterministic_config {
            crypto_key {
              kms_wrapped {
                crypto_key_name = var.tokenize_kms_key
                wrapped_key     = var.tokenize_wrapped_key
              }
            }
            surrogate_info_type {
              name = "PII_TOKEN"
            }
          }
        }
      }
    }
  }
}
