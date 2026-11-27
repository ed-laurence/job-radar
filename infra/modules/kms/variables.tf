variable "project_id" {
  description = "Project that owns the key ring."
  type        = string
}

variable "location" {
  description = "Key ring location."
  type        = string
}

variable "key_ring_name" {
  description = "Key ring name."
  type        = string
}

variable "keys" {
  description = <<-EOT
    Key name => settings. purpose: ENCRYPT_DECRYPT or ASYMMETRIC_SIGN.
    rotation_period applies to symmetric keys only. iam: role => list of principals,
    granted on that key only.
  EOT
  type = map(object({
    purpose          = string
    algorithm        = string
    protection_level = optional(string, "SOFTWARE")
    rotation_period  = optional(string)
    iam              = optional(map(list(string)), {})
  }))
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
