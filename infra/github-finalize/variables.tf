variable "github_owner" {
  description = "GitHub user or organization that owns the repository."
  type        = string
}

variable "github_repo" {
  description = "Repository name (created by infra/bootstrap)."
  type        = string
}

variable "required_checks" {
  description = "Check runs required on main (attached to the head SHA)."
  type        = list(string)
  default     = ["ci", "security-review"]
}

variable "checks_integration_id" {
  description = "App id that must post the required checks (15368 is the public id of the GitHub Actions app), so no other app can satisfy them."
  type        = number
  default     = 15368
}
