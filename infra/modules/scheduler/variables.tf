variable "project_id" {
  description = "Project that owns the schedules."
  type        = string
}

variable "region" {
  description = "Scheduler and Cloud Run region."
  type        = string
}

variable "service_account" {
  description = "Service account email the scheduler uses to call the Cloud Run Admin API."
  type        = string
}

variable "time_zone" {
  description = "Time zone for cron expressions."
  type        = string
  default     = "Etc/UTC"
}

variable "schedules" {
  description = "Schedule name => { job = Cloud Run job name, cron = cron expression, paused = bool }."
  type = map(object({
    job    = string
    cron   = string
    paused = optional(bool, false)
  }))
}
