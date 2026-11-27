variable "project_id" {
  description = "Project that owns the network."
  type        = string
}

variable "region" {
  description = "Region for the subnet."
  type        = string
}

variable "name" {
  description = "Base name for the VPC, subnet and related resources."
  type        = string
}

variable "subnet_cidr" {
  description = "Primary range of the Cloud Run Direct VPC egress subnet."
  type        = string
  default     = "10.10.0.0/24"
}

variable "psa_prefix_length" {
  description = "Prefix length of the range reserved for Private Service Access (Cloud SQL)."
  type        = number
  default     = 20
}

variable "sandbox_tags" {
  description = "Network tags that receive the deny-all egress firewall (sandbox, publisher and red-team jobs)."
  type        = list(string)
  default     = ["sandbox", "redteam"]
}

variable "use_restricted_vip" {
  description = "True when VPC-SC is in use (an Organization exists): Google APIs resolve to restricted.googleapis.com. False uses private.googleapis.com (ADR-0006)."
  type        = bool
}

variable "labels" {
  description = "Labels for labelled resources."
  type        = map(string)
  default     = {}
}
