variable "org_id" {
  description = "Organization id that owns the access policy. The caller creates this module only when an Organization exists (ADR-0006)."
  type        = string
}

variable "project_number" {
  description = "Number of the project placed inside the perimeter."
  type        = string
}

variable "name" {
  description = "Base name for the access policy title and perimeter."
  type        = string
}

variable "restricted_services" {
  description = "Services protected by the perimeter. VPC-accessible services are limited to the same list."
  type        = list(string)
  default = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "dlp.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ]
}

variable "ingress_identities" {
  description = "Principals allowed in from outside the perimeter (deploy and review service accounts, the owner for break-glass). Format: serviceAccount:… or user:…"
  type        = list(string)
}

variable "dry_run" {
  description = "When true the configuration goes into the dry-run spec (violations are logged, not enforced)."
  type        = bool
  default     = false
}
