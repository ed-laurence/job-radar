variable "project_id" {
  description = "Project that owns the service."
  type        = string
}

variable "region" {
  description = "Service region."
  type        = string
}

variable "name" {
  description = "Service name."
  type        = string
}

variable "service_account" {
  description = "Runtime service account email."
  type        = string
}

variable "image" {
  description = "Initial container image. CI deploys digest-pinned images afterwards; OpenTofu ignores later image changes."
  type        = string
}

variable "ingress" {
  description = "INGRESS_TRAFFIC_ALL, INGRESS_TRAFFIC_INTERNAL_ONLY or INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER."
  type        = string

  validation {
    condition     = contains(["INGRESS_TRAFFIC_ALL", "INGRESS_TRAFFIC_INTERNAL_ONLY", "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"], var.ingress)
    error_message = "Unknown ingress setting."
  }
}

variable "invoker_members" {
  description = "Principals granted roles/run.invoker. Only the web service may include allUsers (enforced by infra/policy)."
  type        = list(string)
  default     = []
}

variable "env" {
  description = "Plain environment variables. Never put secrets or identifiers that belong in Secret Manager here."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Environment variables sourced from Secret Manager: name => { secret = secret id, version = version }."
  type = map(object({
    secret  = string
    version = optional(string, "latest")
  }))
  default = {}
}

variable "vpc" {
  description = "Direct VPC egress settings, or null for default Cloud Run egress."
  type = object({
    network    = string
    subnetwork = string
    egress     = optional(string, "ALL_TRAFFIC")
    tags       = optional(list(string), [])
  })
  default = null
}

variable "min_instances" {
  description = "Minimum instances."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum instances (keeps total DB connections under the db-f1-micro limit)."
  type        = number
  default     = 2
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

variable "concurrency" {
  description = "Max concurrent requests per instance."
  type        = number
  default     = 80
}

variable "timeout" {
  description = "Request timeout (SSE streams need a long one)."
  type        = string
  default     = "300s"
}

variable "deletion_protection" {
  description = "Provider-level deletion protection."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
