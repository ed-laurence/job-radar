# Every identifier is a variable. Values live in the untracked
# ~/.config/jobradar/prod.tfvars written by bootstrap.sh.

variable "project_id" {
  description = "New project id (bootstrap.sh appends a random suffix)."
  type        = string
}

variable "project_name" {
  description = "Project display name."
  type        = string
}

variable "org_id" {
  description = "Organization id. Null when no Organization exists (no VPC-SC; ADR-0006)."
  type        = string
  default     = null
}

variable "folder_id" {
  description = "Optional folder id under the Organization. Mutually exclusive with org_id at the API level; when set, org_id is ignored for project placement."
  type        = string
  default     = null
}

variable "billing_account" {
  description = "Billing account id to link."
  type        = string
}

variable "region" {
  description = "Default region."
  type        = string
  default     = "australia-southeast1"
}

variable "labels" {
  description = "Labels applied to labelled resources."
  type        = map(string)
  default     = {}
}

variable "apis" {
  description = "APIs to enable on the project."
  type        = list(string)
  default = [
    "accesscontextmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "cloudbuild.googleapis.com",
    "clouderrorreporting.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "containeranalysis.googleapis.com",
    "containerscanning.googleapis.com",
    "dlp.googleapis.com",
    "dns.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "identitytoolkit.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "serviceusage.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
}

variable "state_bucket_name" {
  description = "GCS bucket for OpenTofu state (bootstrap and prod prefixes)."
  type        = string
}

variable "state_key_ring_name" {
  description = "KMS key ring for state encryption."
  type        = string
  default     = "tofu-state"
}

variable "state_key_name" {
  description = "KMS key for OpenTofu client-side state encryption."
  type        = string
  default     = "state"
}

variable "state_key_users" {
  description = "Extra principals (the owner, for local runs) that may use the state key. Format user:… ."
  type        = list(string)
  default     = []
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository id (container images only)."
  type        = string
  default     = "images"
}

variable "deploy_sa_roles" {
  description = "Project roles for the deploy service account (applies prod)."
  type        = list(string)
  default = [
    "roles/artifactregistry.writer",
    "roles/cloudfunctions.developer",
    "roles/cloudkms.admin",
    "roles/cloudscheduler.admin",
    "roles/cloudsql.admin",
    "roles/cloudtasks.admin",
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/dlp.admin",
    "roles/dns.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/identityplatform.admin",
    "roles/logging.configWriter",
    "roles/modelarmor.admin",
    "roles/monitoring.admin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/run.admin",
    "roles/secretmanager.admin",
    "roles/servicenetworking.networksAdmin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/storage.admin",
  ]
}

variable "budget_amount" {
  description = "Monthly budget in whole units of the billing account currency."
  type        = number
  default     = 50
}

variable "budget_currency" {
  description = "ISO currency code; must match the billing account currency. Null uses the account currency."
  type        = string
  default     = null
}

variable "budget_thresholds" {
  description = "Actual-spend thresholds (fractions) that alert."
  type        = list(number)
  default     = [0.5, 0.9, 1.0]
}

variable "alert_email" {
  description = "Email for budget and monitoring alerts. Owner detail: tfvars only, never committed."
  type        = string
}

# --- GitHub ---
# Staged bootstrap (ADR-0008): with enable_github = false (the GCP-only stage)
# no GitHub resource or data source exists, so the GitHub provider is never
# asked for anything and needs no token. The WIF providers are still created,
# with conditions naming the future repository through the variables below.

variable "enable_github" {
  description = "Manage the GitHub repo, prod environment and Actions variables. False for the GCP-only stage; the full bootstrap sets true."
  type        = bool
  default     = false
}

variable "github_owner" {
  description = "GitHub user or organization that owns (or will own) the repository."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub id of github_owner, pinned in the WIF conditions. Required when enable_github = false; when enable_github = true it is looked up and, if also set, must match."
  type        = string
  default     = null

  validation {
    condition     = var.github_owner_id == null || can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be the numeric GitHub account id, not the login."
  }
}

variable "github_repo" {
  description = "Repository name (it need not exist yet in the GCP-only stage)."
  type        = string
}

variable "github_reviewer" {
  description = "GitHub username who must approve prod deployments. Required when enable_github = true."
  type        = string
  default     = null
}

variable "security_review_workflow" {
  description = "Workflow file that may use the review provider (bound via job_workflow_ref on main)."
  type        = string
  default     = "security-review.yml"
}
