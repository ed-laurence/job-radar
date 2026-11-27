# ADR 0003: Cloud SQL Postgres db-f1-micro on a private IP

Status: accepted

## Context
Dashboard analytics need SQL aggregation; multi-tenancy needs row-level security; live updates need LISTEN/NOTIFY. Firestore and a self-managed VM were considered.

## Decision
Cloud SQL Postgres 16 db-f1-micro (~US$10-12/month), private IP via Private Service Access, Cloud Run via Direct VPC egress (no connector), IAM DB auth, backups + PITR.

## Consequences
About 25 connections: one shared LISTEN per instance, small pools, max-instances caps. No SLA on shared-core, so the SLO is 99% best effort.
