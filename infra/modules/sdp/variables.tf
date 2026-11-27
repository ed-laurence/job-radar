variable "project_id" {
  description = "Project that owns the templates."
  type        = string
}

variable "location" {
  description = "SDP location for the templates."
  type        = string
}

variable "name" {
  description = "Base name for template ids."
  type        = string
}

variable "info_types" {
  description = "Built-in infoTypes to inspect for and de-identify."
  type        = list(string)
  default = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "PERSON_NAME",
    "STREET_ADDRESS",
    "CREDIT_CARD_NUMBER",
    "IP_ADDRESS",
    "DATE_OF_BIRTH",
    "AUSTRALIA_TAX_FILE_NUMBER",
    "AUSTRALIA_MEDICARE_NUMBER",
    "GCP_API_KEY",
    "GCP_CREDENTIALS",
  ]
}

variable "min_likelihood" {
  description = "Minimum likelihood for a finding."
  type        = string
  default     = "POSSIBLE"
}

variable "tokenize_kms_key" {
  description = "KMS key name that wraps the deterministic tokenisation key. Null skips the tokenise template."
  type        = string
  default     = null
}

variable "tokenize_wrapped_key" {
  description = "Base64 data key wrapped by tokenize_kms_key (ciphertext only; useless without KMS decrypt). Supplied via TF_VAR, never committed."
  type        = string
  default     = null
  sensitive   = true
}
