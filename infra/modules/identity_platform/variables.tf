variable "project_id" {
  description = "Project that owns the Identity Platform config."
  type        = string
}

variable "authorized_domains" {
  description = "Domains allowed for sign-in redirects (the web service's run.app host). No custom domain."
  type        = list(string)
  default     = []
}

variable "blocking_function_uri" {
  description = "HTTPS URL of the auth-blocking Cloud Run function (beforeCreate / beforeSignIn). Null until CI has deployed it."
  type        = string
  default     = null
}

variable "totp_adjacent_intervals" {
  description = "Accepted TOTP windows either side of the current one."
  type        = number
  default     = 1
}
