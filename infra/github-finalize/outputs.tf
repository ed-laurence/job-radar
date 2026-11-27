output "ruleset_ids" {
  description = "Ruleset ids."
  value = {
    main_checks = github_repository_ruleset.main_checks.ruleset_id
    main_review = github_repository_ruleset.main_review.ruleset_id
  }
}
