# Prod root, applied by deploy.yml (deploy WIF provider, environment prod).
# State: GCS, encrypted client-side by OpenTofu (encryption.tofu).

terraform {
  required_version = ">= 1.8.0"

  # Partial config: -backend-config="bucket=…" -backend-config="prefix=prod".
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
