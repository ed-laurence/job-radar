# One Model Armor template for both sanitizeUserPrompt (untrusted input) and
# sanitizeModelResponse (output shown to users). The app fails closed when
# Model Armor errors; that logic lives in the ContentGuard adapter.

locals {
  rai_filter_types = ["DANGEROUS", "HARASSMENT", "HATE_SPEECH", "SEXUALLY_EXPLICIT"]
  advanced_sdp     = var.sdp_inspect_template != null
}

resource "google_model_armor_template" "this" {
  project     = var.project_id
  location    = var.location
  template_id = var.template_id

  filter_config {
    rai_settings {
      dynamic "rai_filters" {
        for_each = local.rai_filter_types
        content {
          filter_type      = rai_filters.value
          confidence_level = var.rai_confidence
        }
      }
    }

    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = var.pi_jailbreak_confidence
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    sdp_settings {
      dynamic "basic_config" {
        for_each = local.advanced_sdp ? [] : [1]
        content {
          filter_enforcement = "ENABLED"
        }
      }

      dynamic "advanced_config" {
        for_each = local.advanced_sdp ? [1] : []
        content {
          inspect_template    = var.sdp_inspect_template
          deidentify_template = var.sdp_deidentify_template
        }
      }
    }
  }

  template_metadata {
    enforcement_type        = "INSPECT_AND_BLOCK"
    log_sanitize_operations = true
    log_template_operations = true
  }
}
