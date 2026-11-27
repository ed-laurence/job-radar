# VPC Service Controls: a project-scoped access policy and one perimeter.
# Only instantiated when an Organization exists (see ADR-0006 for the fallback).
#
# A scoped policy (scopes = [project]) avoids clashing with an org-wide policy
# the Organization may already have.

locals {
  project_resource = "projects/${var.project_number}"

  perimeter_config = {
    resources           = [local.project_resource]
    restricted_services = var.restricted_services
  }
}

resource "google_access_context_manager_access_policy" "this" {
  parent = "organizations/${var.org_id}"
  title  = "${var.name}-policy"
  scopes = [local.project_resource]
}

resource "google_access_context_manager_service_perimeter" "this" {
  parent                    = "accessPolicies/${google_access_context_manager_access_policy.this.name}"
  name                      = "accessPolicies/${google_access_context_manager_access_policy.this.name}/servicePerimeters/${replace(var.name, "-", "_")}_perimeter"
  title                     = "${var.name}-perimeter"
  perimeter_type            = "PERIMETER_TYPE_REGULAR"
  use_explicit_dry_run_spec = var.dry_run

  dynamic "status" {
    for_each = var.dry_run ? [] : [local.perimeter_config]
    content {
      resources           = status.value.resources
      restricted_services = status.value.restricted_services

      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = status.value.restricted_services
      }

      ingress_policies {
        title = "ci-and-owner"
        ingress_from {
          identities = var.ingress_identities
          sources {
            access_level = "*"
          }
        }
        ingress_to {
          resources = ["*"]
          operations {
            service_name = "*"
          }
        }
      }
    }
  }

  dynamic "spec" {
    for_each = var.dry_run ? [local.perimeter_config] : []
    content {
      resources           = spec.value.resources
      restricted_services = spec.value.restricted_services

      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = spec.value.restricted_services
      }

      ingress_policies {
        title = "ci-and-owner"
        ingress_from {
          identities = var.ingress_identities
          sources {
            access_level = "*"
          }
        }
        ingress_to {
          resources = ["*"]
          operations {
            service_name = "*"
          }
        }
      }
    }
  }

  # VERIFY (M0 spike): Cloud Scheduler -> run.googleapis.com jobs:run and
  # Cloud Monitoring uptime checks are expected to work with run.googleapis.com
  # restricted; if they are blocked, add an ingress rule for their service agents.
}
