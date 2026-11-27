# Empty harness root: the *.tftest.hcl files here plan other roots and modules
# (run { module { source = … } }) against mock providers.

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
