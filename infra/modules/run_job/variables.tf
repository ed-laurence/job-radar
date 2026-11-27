variable "project_id" {
  description = "Project that owns the job."
  type        = string
}

variable "region" {
  description = "Job region."
  type        = string
}

variable "name" {
  description = "Job name."
  type        = string
}

variable "service_account" {
  description = "Runtime service account email (one per job, least privilege)."
  type        = string
}

variable "image" {
  description = "Initial container image. CI deploys digest-pinned images afterwards; OpenTofu ignores later image changes."
  type        = string
}

variable "args" {
  description = "Container args (e.g. the job entry point)."
  type        = list(string)
  default     = []
}

variable "env" {
  description = "Plain environment variables."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Environment variables sourced from Secret Manager. Must be empty for sandbox jobs (enforced by infra/policy)."
  type = map(object({
    secret  = string
    version = optional(string, "latest")
  }))
  default = {}
}

variable "vpc" {
  description = "Direct VPC egress settings, or null for default Cloud Run egress (only the push job)."
  type = object({
    network    = string
    subnetwork = string
    egress     = optional(string, "ALL_TRAFFIC")
    tags       = optional(list(string), [])
  })
  default = null
}

variable "scratch_volumes" {
  description = "In-memory writable mounts (the image itself runs as non-root)."
  type        = map(string)
  default = {
    workspace = "/workspace"
    tmp       = "/tmp"
  }
}

variable "scratch_size_limit" {
  description = "Size limit for each in-memory scratch volume."
  type        = string
  default     = "1Gi"
}

variable "cpu" {
  description = "CPU limit."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit."
  type        = string
  default     = "512Mi"
}

variable "timeout" {
  description = "Task timeout."
  type        = string
  default     = "600s"
}

variable "max_retries" {
  description = "Retries per task."
  type        = number
  default     = 0
}

variable "task_count" {
  description = "Tasks per execution."
  type        = number
  default     = 1
}

variable "executor_members" {
  description = "Principals that may run this job with overrides (roles/run.jobsExecutorWithOverrides)."
  type        = list(string)
  default     = []
}

variable "invoker_members" {
  description = "Principals that may run this job as configured (roles/run.invoker), e.g. the scheduler."
  type        = list(string)
  default     = []
}

variable "deletion_protection" {
  description = "Provider-level deletion protection."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
