# ADR 0002: GCP: Cloud Run + Vertex AI, local and prod only

Status: accepted

## Context
Low cost, two users, minimal operations. Claude, Gemini and open models are all needed from one cloud with IAM auth.

## Decision
Cloud Run services and jobs (scale to zero) in one prod project. Vertex AI for Claude (AnthropicVertex), Gemini (reviewers) and Model Garden MaaS open models. Environments: local (scripts/dev.sh) and prod only; canary deploys via Cloud Run traffic splitting.

## Consequences
No staging environment: CI e2e tests run against a temporary Postgres, and canaries protect prod. Vertex has no Managed Agents or server-side fallbacks, so agent loops are self-hosted and refusal fallbacks happen client-side.
