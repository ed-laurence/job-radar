variable "project_id" {
  description = "Project that owns the instance."
  type        = string
}

variable "region" {
  description = "Instance region."
  type        = string
}

variable "instance_name" {
  description = "Cloud SQL instance name."
  type        = string
}

variable "database_name" {
  description = "Application database name."
  type        = string
}

variable "tier" {
  description = "Machine tier. db-f1-micro allows about 25 connections; app pools are sized for it."
  type        = string
  default     = "db-f1-micro"
}

variable "network_id" {
  description = "VPC id for the private IP (Private Service Access)."
  type        = string
}

variable "psa_connection" {
  description = "Private Service Access connection id; passed so the instance waits for the peering."
  type        = string
}

variable "iam_service_accounts" {
  description = "Service account emails that get a CLOUD_IAM_SERVICE_ACCOUNT database user."
  type        = list(string)
  default     = []
}

variable "backup_start_time" {
  description = "Daily backup window start (UTC, HH:MM)."
  type        = string
  default     = "16:00"
}

variable "retained_backups" {
  description = "Number of automated backups kept."
  type        = number
  default     = 7
}

variable "transaction_log_retention_days" {
  description = "Days of WAL kept for point-in-time recovery."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Protect the instance from deletion (both the provider flag and the API flag)."
  type        = bool
  default     = true
}

variable "labels" {
  description = "User labels."
  type        = map(string)
  default     = {}
}
