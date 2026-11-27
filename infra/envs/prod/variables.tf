# Every identifier is a variable with no realistic default. Real values come
# from the untracked ~/.config/jobradar/prod.tfvars (local) or GitHub
# environment variables (CI). See terraform.tfvars.example.

variable "project_id" {
  description = "Project id (from infra/bootstrap)."
  type        = string
}

variable "project_number" {
  description = "Project number (from infra/bootstrap)."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Cloud SQL, buckets and templates."
  type        = string
  default     = "australia-southeast1"
}

variable "org_id" {
  description = "Organization id. Set => VPC-SC perimeter + restricted VIP; null => deny-all + Private Google Access fallback (ADR-0006)."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Short prefix for resource and service-account names."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.name_prefix))
    error_message = "name_prefix must be 2-10 lowercase alphanumerics starting with a letter (service-account ids are limited to 30 chars)."
  }
}

variable "labels" {
  description = "Labels for labelled resources."
  type        = map(string)
  default     = {}
}

variable "state_encryption_key" {
  description = "KMS key id used by OpenTofu state encryption (read only by encryption.tofu)."
  type        = string
}

variable "deploy_service_account" {
  description = "Deploy service account email (from bootstrap); allowed into the VPC-SC perimeter."
  type        = string
}

variable "review_service_account" {
  description = "Review service account email (from bootstrap); allowed into the VPC-SC perimeter."
  type        = string
}

variable "perimeter_admin_members" {
  description = "Extra principals allowed into the perimeter for break-glass access (user:…)."
  type        = list(string)
  default     = []
}

variable "vpc_sc_dry_run" {
  description = "Put the perimeter in dry-run mode (log only)."
  type        = bool
  default     = false
}

variable "notification_channels" {
  description = "Monitoring notification channel ids (from bootstrap)."
  type        = list(string)
}

variable "placeholder_image" {
  description = "Image used when OpenTofu first creates services and jobs; CI then deploys digest-pinned images."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "uploads_bucket_name" {
  description = "Bucket for uploads and generated PDFs."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Bucket for agent bundles, results, verdicts and handoffs."
  type        = string
}

variable "artifact_retention_days" {
  description = "Days before agent artifacts are deleted."
  type        = number
  default     = 30
}

variable "database_name" {
  description = "Application database name."
  type        = string
  default     = "app"
}

variable "secret_ids" {
  description = "Secret Manager secret ids (containers only; values are set outside OpenTofu)."
  type = object({
    app_users       = optional(string, "app-users")
    denylist        = optional(string, "denylist")
    deploy_key      = optional(string, "deploy-key")
    webhook_hmac    = optional(string, "webhook-hmac")
    coordinator_key = optional(string, "coordinator-token-key")
  })
  default = {}
}

variable "blocking_function_uri" {
  description = "URL of the auth-blocking Cloud Run function. Null until CI has deployed it."
  type        = string
  default     = null
}

variable "extra_authorized_domains" {
  description = "Extra Identity Platform authorized domains (the web run.app host is added automatically)."
  type        = list(string)
  default     = []
}

variable "model_armor_location" {
  description = "Model Armor location; null uses var.region (checked in the M0 spike)."
  type        = string
  default     = null
}

variable "sdp_tokenize_kms_key" {
  description = "KMS key that wraps the SDP tokenisation key; null skips the tokenise template."
  type        = string
  default     = null
}

variable "sdp_tokenize_wrapped_key" {
  description = "KMS-wrapped SDP data key (ciphertext). Supplied via TF_VAR only."
  type        = string
  default     = null
  sensitive   = true
}

variable "redteam_enabled" {
  description = "REDTEAM_ENABLED: unpauses the weekly red-team schedule."
  type        = bool
  default     = false
}

variable "time_zone" {
  description = "Time zone for schedules."
  type        = string
  default     = "Etc/UTC"
}

variable "schedules" {
  description = "Cron expressions per scheduled job."
  type = object({
    feed_replay = optional(string, "*/15 * * * *") # the scan (app.jobs.scan); key kept for main.tf
    digest      = optional(string, "0 20 * * *")
    rollup      = optional(string, "0 21 * * *")
    watchdog    = optional(string, "*/15 * * * *")
    evals       = optional(string, "0 19 * * *")
    redteam     = optional(string, "0 17 * * 3")
  })
  default = {}
}

variable "max_instances" {
  description = "Cloud Run max instances (api pool 5 + coordinator pool 2 per instance; keep total DB connections < 20)."
  type = object({
    web         = optional(number, 3)
    api         = optional(number, 2)
    coordinator = optional(number, 1)
  })
  default = {}
}
