# Bootstrap root. Run once by the owner via scripts/bootstrap.sh, before the
# initial commit. State starts local; bootstrap.sh then writes an untracked
# backend_override.tf pointing at the state bucket and runs
# `tofu init -migrate-state`. See encryption.tofu for the two-pass flow.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}

provider "google" {
  region = var.region
}

# Billing Budgets API calls need a quota project; the new project serves.
provider "google" {
  alias                 = "billing"
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

# Token comes from GITHUB_TOKEN (bootstrap.sh exports `gh auth token`) in the
# full run only. With enable_github = false nothing uses this provider and the
# owner is left unset, so it is configured anonymously and needs no token.
provider "github" {
  owner = var.enable_github ? var.github_owner : null
}
