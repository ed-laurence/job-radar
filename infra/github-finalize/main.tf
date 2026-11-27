# Repository rulesets, applied AFTER the initial commit has been pushed
# (deploy.yml's last step or `bootstrap.sh --finalize`), so they don't block
# that first push. Separate root with its own state (prefix "github-finalize").
#
# Two rulesets because a bypass actor applies to a whole ruleset:
# - main-checks: required checks, no force-push, no deletion. Nobody bypasses.
# - main-review: one approval. Repository admins may bypass it only when
#   merging a pull request (a solo owner can't approve their own PR); agent
#   PRs, authored by the github-actions bot, still need the owner's approval.

terraform {
  required_version = ">= 1.8.0"

  # Partial config: -backend-config="bucket=…" -backend-config="prefix=github-finalize".
  backend "gcs" {}

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}

provider "github" {
  owner = var.github_owner
}

locals {
  repository_admin_role_id = 5
}

resource "github_repository_ruleset" "main_checks" {
  name        = "main-checks"
  repository  = var.github_repo
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true

    required_status_checks {
      strict_required_status_checks_policy = true

      dynamic "required_check" {
        for_each = toset(var.required_checks)
        content {
          context        = required_check.value
          integration_id = var.checks_integration_id
        }
      }
    }
  }
}

resource "github_repository_ruleset" "main_review" {
  name        = "main-review"
  repository  = var.github_repo
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  bypass_actors {
    actor_id    = local.repository_admin_role_id
    actor_type  = "RepositoryRole"
    bypass_mode = "pull_request"
  }

  rules {
    pull_request {
      required_approving_review_count   = 1
      dismiss_stale_reviews_on_push     = true
      require_last_push_approval        = true
      required_review_thread_resolution = true
    }
  }
}
