# Identity Platform: no self-registration, email/password only, TOTP MFA,
# blocking functions for beforeCreate (always refuses) and beforeSignIn
# (exact APP_USERS match + email_verified). Accounts are created by the
# provision-users job, never through sign-up.
#
# The config's client.api_key is the public-by-design web API key; it is never
# exported as an output.

resource "google_identity_platform_config" "this" {
  project                    = var.project_id
  autodelete_anonymous_users = true
  authorized_domains         = var.authorized_domains

  sign_in {
    allow_duplicate_emails = false

    email {
      enabled           = true
      password_required = true
    }

    anonymous {
      enabled = false
    }

    phone_number {
      enabled = false
    }
  }

  client {
    permissions {
      disabled_user_signup   = true
      disabled_user_deletion = true
    }
  }

  mfa {
    state = "ENABLED"

    provider_configs {
      state = "ENABLED"
      totp_provider_config {
        adjacent_intervals = var.totp_adjacent_intervals
      }
    }
  }

  dynamic "blocking_functions" {
    for_each = var.blocking_function_uri == null ? [] : [var.blocking_function_uri]
    content {
      triggers {
        event_type   = "beforeCreate"
        function_uri = blocking_functions.value
      }
      triggers {
        event_type   = "beforeSignIn"
        function_uri = blocking_functions.value
      }
      forward_inbound_credentials {
        id_token = true
      }
    }
  }

  monitoring {
    request_logging {
      enabled = true
    }
  }
}
