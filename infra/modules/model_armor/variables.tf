variable "project_id" {
  description = "Project that owns the template."
  type        = string
}

variable "location" {
  description = "Model Armor location. Checked in the M0 spike; use the nearest supported region if the app region is not."
  type        = string
}

variable "template_id" {
  description = "Template id used for sanitizeUserPrompt and sanitizeModelResponse."
  type        = string
}

variable "rai_confidence" {
  description = "Confidence threshold for responsible-AI filters."
  type        = string
  default     = "MEDIUM_AND_ABOVE"
}

variable "pi_jailbreak_confidence" {
  description = "Confidence threshold for prompt-injection / jailbreak detection."
  type        = string
  default     = "LOW_AND_ABOVE"
}

variable "sdp_inspect_template" {
  description = "Full SDP inspect template name for advanced SDP screening, or null for the basic config."
  type        = string
  default     = null
}

variable "sdp_deidentify_template" {
  description = "Full SDP de-identify template name used with sdp_inspect_template, or null."
  type        = string
  default     = null
}
