variable "project_id" {
  description = "Project that owns the secrets."
  type        = string
}

variable "replica_location" {
  description = "Single user-managed replica location (data residency)."
  type        = string
}

variable "secrets" {
  description = "Secret id => list of principals granted roles/secretmanager.secretAccessor on that secret only."
  type        = map(list(string))
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
