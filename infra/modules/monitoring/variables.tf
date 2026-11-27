variable "project_id" {
  description = "Project that owns the monitoring resources."
  type        = string
}

variable "name" {
  description = "Base name for display names and ids."
  type        = string
}

variable "notification_channels" {
  description = "Notification channel ids for every alert policy (created in bootstrap)."
  type        = list(string)
}

variable "uptime_checks" {
  description = "Check name => { uri = https URL of a run.app service, path, oidc = true when the service requires IAM (service-agent OIDC token) }."
  type = map(object({
    uri  = string
    path = string
    oidc = optional(bool, false)
  }))
}

variable "availability_slos" {
  description = "SLO name => Cloud Run service name, measured on built-in request_count (5xx = bad)."
  type        = map(string)
}

variable "availability_goal" {
  description = "Availability SLO target (best effort on db-f1-micro)."
  type        = number
  default     = 0.99
}

variable "latency_goal" {
  description = "Fraction of requests that must meet the latency thresholds (p95)."
  type        = number
  default     = 0.95
}

variable "non_ai_latency_ms" {
  description = "Latency threshold for non-AI endpoints."
  type        = number
  default     = 300
}

variable "ai_ttft_ms" {
  description = "Time-to-first-token threshold for AI endpoints."
  type        = number
  default     = 3000
}

variable "rolling_period_days" {
  description = "SLO rolling window."
  type        = number
  default     = 28
}
